/* Native-only scheduling experiment. The calling C thread launches CUDA via a
 * C callback, while persistent Win32 workers allocate and scan CPU chunks.
 * A single SRW lock protects allocation/accounting; hashing and CUDA execute
 * outside it. No process IPC, Python polling, or Python callback is involved. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <openssl/crypto.h>
#include <openssl/sha.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "native_scheduler.h"

_Static_assert(sizeof(HCScanResult) == 96, "HCScanResult ABI changed");
_Static_assert(sizeof(HCTrace) == 136, "HCTrace ABI changed");

typedef struct {
    struct HCCoordinator *ctx;
    uint32_t index;
    HANDLE handle;
} HCWorker;

struct HCCoordinator {
    SRWLOCK lock;
    CONDITION_VARIABLE changed;
    SHA256_CTX prefix;
    HCWorker *worker;
    HCTrace *trace;
    uint32_t trace_capacity, difficulty, generation;
    uint32_t configured, shutdown, cpu_active, gpu_finished, gpu_present;
    uint32_t stop_assign;
    uint64_t cpu_batch, gpu_batch, next, remaining, tail_guard;
    uint64_t retry_next, retry_remaining;
    double frequency;
    HCStats stats;
};

static double now_seconds(const HCCoordinator *ctx) {
    LARGE_INTEGER value;
    QueryPerformanceCounter(&value);
    return (double)value.QuadPart / ctx->frequency;
}

static size_t decimal(uint64_t value, unsigned char digits[20]) {
    unsigned char reversed[20];
    size_t length = 0;
    do {
        reversed[length++] = (unsigned char)('0' + value % 10);
        value /= 10;
    } while (value);
    for (size_t i = 0; i < length; ++i) digits[i] = reversed[length - 1 - i];
    return length;
}

static size_t next_decimal(unsigned char digits[20], size_t length) {
    size_t i = length;
    while (i && digits[i - 1] == '9') digits[--i] = '0';
    if (i) {
        ++digits[i - 1];
        return length;
    }
    memmove(digits + 1, digits, length);
    digits[0] = '1';
    return length + 1;
}

static int matches(const unsigned char digest[32], uint32_t difficulty) {
    uint32_t bytes = difficulty / 2;
    for (uint32_t i = 0; i < bytes; ++i) if (digest[i]) return 0;
    return !(difficulty & 1) || digest[bytes] < 0x10;
}

static int hash_nonce(const HCCoordinator *ctx, uint64_t nonce,
                      unsigned char digest[32]) {
    unsigned char digits[20];
    size_t length = decimal(nonce, digits);
    SHA256_CTX current = ctx->prefix;
    return SHA256_Update(&current, digits, length) &&
           SHA256_Final(digest, &current);
}

static int cpu_scan(const HCCoordinator *ctx, uint64_t start, uint64_t count,
                    HCScanResult *result) {
    memset(result, 0, sizeof(*result));
    unsigned char digits[20];
    size_t length = decimal(start, digits);
    uint64_t nonce = start;
    for (uint64_t i = 0; i < count; ++i) {
        SHA256_CTX current = ctx->prefix;
        if (!SHA256_Update(&current, digits, length) ||
            !SHA256_Final(result->last_hash, &current)) return 2;
        result->checked = i + 1;
        result->last_nonce = nonce;
        if (matches(result->last_hash, ctx->difficulty)) {
            if (!result->found) {
                result->found = 1;
                result->nonce = nonce;
                memcpy(result->hash, result->last_hash, 32);
            }
            if (ctx->stats.search) return 0;
        }
        if (i + 1 < count) {
            ++nonce;
            length = next_decimal(digits, length);
        }
    }
    return 0;
}

/* Called before accounting, outside the allocation lock. Proof and last digest
 * are rehashed using host OpenSSL, independently of the CUDA implementation. */
static int valid_result(const HCCoordinator *ctx, uint64_t start, uint64_t count,
                        const HCScanResult *result) {
    if (!result->checked || result->checked > count || result->found > 1 ||
        (result->checked != count && !(ctx->stats.search && result->found)) ||
        result->last_nonce != start + result->checked - 1) return 0;
    unsigned char digest[32];
    if (!hash_nonce(ctx, result->last_nonce, digest) ||
        memcmp(digest, result->last_hash, 32)) return 0;
    if (result->found) {
        if (result->nonce < start || result->nonce - start >= result->checked ||
            !hash_nonce(ctx, result->nonce, digest) ||
            memcmp(digest, result->hash, 32) ||
            !matches(digest, ctx->difficulty)) return 0;
    }
    return 1;
}

static void fatal_locked(HCCoordinator *ctx, const char *error) {
    if (!ctx->stats.status) {
        ctx->stats.status = 2;
        strncpy_s(ctx->stats.error, sizeof(ctx->stats.error), error, _TRUNCATE);
    }
    ctx->stop_assign = 1;
    WakeAllConditionVariable(&ctx->changed);
}

static void trace_locked(HCCoordinator *ctx, uint32_t worker, uint32_t kind,
    uint64_t start, uint64_t count, double begin, double end,
    double kernel_seconds, int status, const HCScanResult *result) {
    if (ctx->stats.trace_count >= ctx->trace_capacity) {
        ++ctx->stats.trace_dropped;
        return;
    }
    HCTrace *trace = &ctx->trace[ctx->stats.trace_count++];
    memset(trace, 0, sizeof(*trace));
    trace->start = start;
    trace->count = count;
    trace->begin = begin;
    trace->end = end;
    trace->kernel_seconds = kernel_seconds;
    trace->worker = worker;
    trace->kind = kind;
    trace->status = (uint32_t)status;
    if (!status) {
        trace->checked = result->checked;
        trace->found = result->found;
        trace->nonce = result->nonce;
        memcpy(trace->hash, result->hash, 32);
        memcpy(trace->last_hash, result->last_hash, 32);
    }
}

static void accept_locked(HCCoordinator *ctx, const HCScanResult *result,
                          uint32_t kind, double begin, double end,
                          double kernel_seconds) {
    ctx->stats.checked += result->checked;
    ctx->stats.last_nonce = result->last_nonce;
    memcpy(ctx->stats.last_hash, result->last_hash, 32);
    if (kind == 1) {
        ctx->stats.cpu_checked += result->checked;
        ++ctx->stats.cpu_batches;
        ctx->stats.cpu_seconds += end - begin;
    } else {
        ctx->stats.gpu_checked += result->checked;
        ++ctx->stats.gpu_batches;
        ctx->stats.gpu_seconds += end - begin;
        ctx->stats.gpu_kernel_seconds += kernel_seconds;
    }
    if (result->found && !ctx->stats.found) {
        ctx->stats.found = 1;
        ctx->stats.nonce = result->nonce;
        memcpy(ctx->stats.hash, result->hash, 32);
        ctx->stats.first_proof_seconds = now_seconds(ctx) - ctx->stats.begin;
        if (ctx->stats.search) {
            ctx->stop_assign = 1;
            WakeAllConditionVariable(&ctx->changed);
        }
    }
}

/* Clamp before moving the shared cursor: each CUDA kernel specializes decimal
 * width, so a reservation must never straddle 9/10, 99/100, ... boundaries. */
static uint64_t decimal_chunk(uint64_t start, uint64_t count) {
    uint64_t boundary = 10;
    while (boundary <= start && boundary <= UINT64_MAX / 10) boundary *= 10;
    if (boundary > start && count > boundary - start) return boundary - start;
    return count;
}

static int reserve_locked(HCCoordinator *ctx, uint32_t kind,
                          uint64_t *start, uint64_t *count) {
    if (ctx->stop_assign) return 0;
    uint64_t batch = kind == 1 ? ctx->cpu_batch : ctx->gpu_batch;
    if (kind == 1 && ctx->retry_remaining) {
        *start = ctx->retry_next;
        *count = ctx->retry_remaining < batch ? ctx->retry_remaining : batch;
        ctx->retry_remaining -= *count;
        if (ctx->retry_remaining) ctx->retry_next += *count;
        return 1;
    }
    if (!ctx->remaining) return 0;
    if (kind == 1 && ctx->gpu_present && !ctx->gpu_finished &&
        ctx->remaining <= ctx->tail_guard) return 0;
    *start = ctx->next;
    *count = ctx->remaining < batch ? ctx->remaining : batch;
    if (kind == 1 && ctx->gpu_present && !ctx->gpu_finished &&
        *count > ctx->remaining - ctx->tail_guard)
        *count = ctx->remaining - ctx->tail_guard;
    if (kind == 2) *count = decimal_chunk(*start, *count);
    ctx->remaining -= *count;
    ctx->stats.assigned += *count;
    if (ctx->remaining) ctx->next += *count;
    return 1;
}

static DWORD WINAPI worker_main(void *argument) {
    HCWorker *worker = (HCWorker *)argument;
    HCCoordinator *ctx = worker->ctx;
    uint32_t seen_generation = 0;
    AcquireSRWLockExclusive(&ctx->lock);
    ++ctx->stats.workers_ready;
    WakeAllConditionVariable(&ctx->changed);
    for (;;) {
        while (!ctx->shutdown &&
            (!ctx->stats.running || ctx->generation == seen_generation))
            SleepConditionVariableSRW(&ctx->changed, &ctx->lock, INFINITE, 0);
        if (ctx->shutdown) break;
        seen_generation = ctx->generation;
        for (;;) {
            uint64_t start, count;
            if (!reserve_locked(ctx, 1, &start, &count)) {
                if (ctx->stop_assign || ctx->gpu_finished) break;
                /* A still-running GPU might fail and return a reserved range;
                 * park at the condition variable rather than exit or spin. */
                SleepConditionVariableSRW(&ctx->changed, &ctx->lock, INFINITE, 0);
                continue;
            }
            ReleaseSRWLockExclusive(&ctx->lock);
            HCScanResult result;
            double begin = now_seconds(ctx);
            int status = cpu_scan(ctx, start, count, &result);
            double end = now_seconds(ctx);
            if (!status && !valid_result(ctx, start, count, &result)) status = 3;
            AcquireSRWLockExclusive(&ctx->lock);
            trace_locked(ctx, worker->index, 1, start, count, begin, end, 0,
                         status, &result);
            if (status) fatal_locked(ctx, "Native CPU scan or validation failed");
            else accept_locked(ctx, &result, 1, begin, end, 0);
        }
        --ctx->cpu_active;
        WakeAllConditionVariable(&ctx->changed);
    }
    ReleaseSRWLockExclusive(&ctx->lock);
    return 0;
}

HC_EXPORT HCCoordinator *HC_CALL hc_create(const unsigned char *message,
    size_t message_length, uint32_t difficulty, uint32_t workers,
    uint64_t cpu_batch, uint64_t gpu_batch, uint32_t trace_capacity) {
    if ((!message && message_length) || difficulty < 1 || difficulty > 64 ||
        workers > 64 || !cpu_batch || !gpu_batch) return NULL;
    HCCoordinator *ctx = (HCCoordinator *)calloc(1, sizeof(*ctx));
    if (!ctx) return NULL;
    InitializeSRWLock(&ctx->lock);
    InitializeConditionVariable(&ctx->changed);
    LARGE_INTEGER frequency;
    if (!QueryPerformanceFrequency(&frequency)) { free(ctx); return NULL; }
    ctx->frequency = (double)frequency.QuadPart;
    ctx->difficulty = difficulty;
    ctx->cpu_batch = cpu_batch;
    ctx->gpu_batch = gpu_batch;
    ctx->trace_capacity = trace_capacity;
    ctx->stats.abi_version = 1;
    ctx->stats.qpc_frequency = ctx->frequency;
    ctx->stats.workers = workers;
    if (!SHA256_Init(&ctx->prefix) ||
        !SHA256_Update(&ctx->prefix, message, message_length)) { free(ctx); return NULL; }
    if (trace_capacity) {
        ctx->trace = (HCTrace *)calloc(trace_capacity, sizeof(HCTrace));
        if (!ctx->trace) { free(ctx); return NULL; }
    }
    if (workers) {
        ctx->worker = (HCWorker *)calloc(workers, sizeof(HCWorker));
        if (!ctx->worker) { free(ctx->trace); free(ctx); return NULL; }
        for (uint32_t i = 0; i < workers; ++i) {
            ctx->worker[i].ctx = ctx;
            ctx->worker[i].index = i;
            ctx->worker[i].handle = CreateThread(NULL, 0, worker_main,
                                                &ctx->worker[i], 0, NULL);
            if (!ctx->worker[i].handle) {
                AcquireSRWLockExclusive(&ctx->lock);
                ctx->shutdown = 1;
                WakeAllConditionVariable(&ctx->changed);
                ReleaseSRWLockExclusive(&ctx->lock);
                for (uint32_t j = 0; j < i; ++j) {
                    WaitForSingleObject(ctx->worker[j].handle, INFINITE);
                    CloseHandle(ctx->worker[j].handle);
                }
                free(ctx->worker); free(ctx->trace); free(ctx);
                return NULL;
            }
        }
        AcquireSRWLockExclusive(&ctx->lock);
        while (ctx->stats.workers_ready != workers)
            SleepConditionVariableSRW(&ctx->changed, &ctx->lock, INFINITE, 0);
        ReleaseSRWLockExclusive(&ctx->lock);
    }
    return ctx;
}

HC_EXPORT int HC_CALL hc_reset(HCCoordinator *ctx, uint64_t start,
    uint64_t count, uint32_t search, uint64_t tail_guard) {
    if (!ctx || !count || search > 1 || count - 1 > UINT64_MAX - start ||
        (search && tail_guard)) return 1;
    AcquireSRWLockExclusive(&ctx->lock);
    if (ctx->stats.running) { ReleaseSRWLockExclusive(&ctx->lock); return 1; }
    uint32_t workers = ctx->stats.workers;
    uint32_t ready = ctx->stats.workers_ready;
    memset(&ctx->stats, 0, sizeof(ctx->stats));
    ctx->stats.abi_version = 1;
    ctx->stats.workers = workers;
    ctx->stats.workers_ready = ready;
    ctx->stats.search = search;
    ctx->stats.range_start = start;
    ctx->stats.range_count = count;
    ctx->stats.qpc_frequency = ctx->frequency;
    ctx->next = start;
    ctx->remaining = count;
    ctx->tail_guard = tail_guard;
    ctx->retry_remaining = 0;
    ctx->stop_assign = 0;
    ctx->configured = 1;
    ReleaseSRWLockExclusive(&ctx->lock);
    return 0;
}

HC_EXPORT int HC_CALL hc_run(HCCoordinator *ctx, HCGpuScan callback, void *user) {
    if (!ctx) return 1;
    AcquireSRWLockExclusive(&ctx->lock);
    if (!ctx->configured || ctx->stats.running ||
        (!callback && !ctx->stats.workers)) {
        ReleaseSRWLockExclusive(&ctx->lock); return 1;
    }
    ctx->configured = 0;
    ctx->gpu_present = callback != NULL;
    ctx->gpu_finished = callback == NULL;
    ctx->cpu_active = ctx->stats.workers;
    ctx->stats.running = 1;
    ctx->stats.begin = now_seconds(ctx);
    ++ctx->generation;
    WakeAllConditionVariable(&ctx->changed);
    while (callback) {
        uint64_t start, count;
        if (!reserve_locked(ctx, 2, &start, &count)) break;
        ReleaseSRWLockExclusive(&ctx->lock);
        HCScanResult result;
        memset(&result, 0, sizeof(result));
        double kernel_seconds = 0;
        double begin = now_seconds(ctx);
        int status = callback(user, start, count, &result, &kernel_seconds);
        double end = now_seconds(ctx);
        if (!status && (!valid_result(ctx, start, count, &result) ||
            !isfinite(kernel_seconds) || kernel_seconds < 0 ||
            kernel_seconds > end - begin + 0.001)) status = 3;
        AcquireSRWLockExclusive(&ctx->lock);
        trace_locked(ctx, UINT32_MAX, 2, start, count, begin, end,
                     kernel_seconds, status, &result);
        if (status) {
            ctx->stats.gpu_failed = 1;
            if (!ctx->stats.workers && !ctx->stop_assign)
                fatal_locked(ctx, "GPU callback failed and no CPU worker is available");
            else if (!ctx->stop_assign) {
                ctx->retry_next = start;
                ctx->retry_remaining = count;
                ++ctx->stats.retries;
            }
            break;  /* Disable GPU after failure; CPU drains retry and tail. */
        }
        accept_locked(ctx, &result, 2, begin, end, kernel_seconds);
    }
    ctx->gpu_finished = 1;
    WakeAllConditionVariable(&ctx->changed);
    while (ctx->cpu_active)
        SleepConditionVariableSRW(&ctx->changed, &ctx->lock, INFINITE, 0);
    ctx->stats.end = now_seconds(ctx);
    ctx->stats.elapsed = ctx->stats.end - ctx->stats.begin;
    if (!ctx->stats.cancelled && !(ctx->stats.search && ctx->stats.found) &&
        ctx->stats.checked != ctx->stats.range_count)
        fatal_locked(ctx, "Native scheduler finished without complete unique coverage");
    ctx->stats.running = 0;
    int status = (int)ctx->stats.status;
    WakeAllConditionVariable(&ctx->changed);
    ReleaseSRWLockExclusive(&ctx->lock);
    return status;
}

HC_EXPORT void HC_CALL hc_cancel(HCCoordinator *ctx) {
    if (!ctx) return;
    AcquireSRWLockExclusive(&ctx->lock);
    if (ctx->stats.running) {
        ctx->stats.cancelled = 1;
        ctx->stop_assign = 1;
        WakeAllConditionVariable(&ctx->changed);
    }
    ReleaseSRWLockExclusive(&ctx->lock);
}

HC_EXPORT int HC_CALL hc_snapshot(HCCoordinator *ctx, HCStats *result) {
    if (!ctx || !result) return 1;
    AcquireSRWLockShared(&ctx->lock);
    *result = ctx->stats;
    if (result->running) result->elapsed = now_seconds(ctx) - result->begin;
    ReleaseSRWLockShared(&ctx->lock);
    return 0;
}

HC_EXPORT uint32_t HC_CALL hc_copy_traces(HCCoordinator *ctx, HCTrace *out,
                                         uint32_t capacity) {
    if (!ctx || (!out && capacity)) return 0;
    AcquireSRWLockShared(&ctx->lock);
    uint32_t count = ctx->stats.trace_count < capacity ? ctx->stats.trace_count : capacity;
    if (count) memcpy(out, ctx->trace, sizeof(HCTrace) * count);
    ReleaseSRWLockShared(&ctx->lock);
    return count;
}

HC_EXPORT void HC_CALL hc_destroy(HCCoordinator *ctx) {
    if (!ctx) return;
    hc_cancel(ctx);
    AcquireSRWLockExclusive(&ctx->lock);
    while (ctx->stats.running)
        SleepConditionVariableSRW(&ctx->changed, &ctx->lock, INFINITE, 0);
    ctx->shutdown = 1;
    WakeAllConditionVariable(&ctx->changed);
    ReleaseSRWLockExclusive(&ctx->lock);
    for (uint32_t i = 0; i < ctx->stats.workers; ++i) {
        WaitForSingleObject(ctx->worker[i].handle, INFINITE);
        CloseHandle(ctx->worker[i].handle);
    }
    free(ctx->worker);
    free(ctx->trace);
    free(ctx);
}

HC_EXPORT uint32_t HC_CALL hc_scheduler_abi(void) { return 1; }
HC_EXPORT const char *HC_CALL hc_scheduler_openssl_version(void) {
    return OpenSSL_version(OPENSSL_VERSION);
}
HC_EXPORT const char *HC_CALL hc_scheduler_cpu_info(void) {
    return OpenSSL_version(OPENSSL_CPU_INFO);
}
