/* CUDA Driver API bridge for the full native scheduler experiment. The
 * generated SHA-256 kernel and its launch geometry are unchanged. No CUDA SDK
 * link library is needed: nvcuda.dll is provided by the installed driver. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <openssl/sha.h>
#include "native_cuda_driver.h"

typedef int (__stdcall *CtxGetCurrent)(void **);
typedef int (__stdcall *CtxPushCurrent)(void *);
typedef int (__stdcall *CtxPopCurrent)(void **);
typedef int (__stdcall *MemsetD32Async)(uint64_t, unsigned int, size_t, void *);
typedef int (__stdcall *LaunchKernel)(void *, unsigned int, unsigned int, unsigned int,
    unsigned int, unsigned int, unsigned int, unsigned int, void *, void **, void **);
typedef int (__stdcall *MemcpyDtoHAsync)(void *, uint64_t, size_t, void *);
typedef int (__stdcall *StreamSynchronize)(void *);
typedef int (__stdcall *EventRecord)(void *, void *);
typedef int (__stdcall *EventElapsedTime)(float *, void *, void *);
typedef int (__stdcall *GetErrorString)(int, const char **);

typedef struct {
    HMODULE dll;
    CtxGetCurrent get_current;
    CtxPushCurrent push;
    CtxPopCurrent pop;
    MemsetD32Async clear;
    LaunchKernel launch;
    MemcpyDtoHAsync copy;
    StreamSynchronize sync;
    EventRecord record;
    EventElapsedTime elapsed;
    GetErrorString error_string;
    void *context, *stream, *functions[20], *host, *begin, *end;
    uint64_t device;
    uint32_t threads, per_thread, difficulty;
    SHA256_CTX prefix;
    char error[256];
} NCCuda;

static int failure(NCCuda *ctx, const char *operation, int code) {
    const char *detail = "unknown CUDA error";
    if (ctx->error_string) ctx->error_string(code, &detail);
    snprintf(ctx->error, sizeof(ctx->error), "%s: CUDA %d: %s", operation, code,
             detail ? detail : "unknown");
    return code ? code : -1;
}

#define LOAD(member, type, symbol) do { \
    ctx->member = (type)(void *)GetProcAddress(ctx->dll, symbol); \
    if (!ctx->member) goto fail; \
} while (0)

HC_EXPORT void *HC_CALL nc_create(void *cuda_context, void *stream,
    const uintptr_t functions[20], uint64_t result_device, void *result_host,
    void *begin_event, void *end_event, uint32_t threads, uint32_t per_thread,
    const unsigned char *message, size_t message_length, uint32_t difficulty) {
    if (!cuda_context || !stream || !functions || !result_device || !result_host ||
        !begin_event || !end_event || !threads || !per_thread || per_thread > 65536 ||
        difficulty < 1 || difficulty > 64 || message_length > 35 || (!message && message_length)) return NULL;
    NCCuda *ctx = calloc(1, sizeof(*ctx));
    if (!ctx) return NULL;
    ctx->dll = LoadLibraryW(L"nvcuda.dll");
    if (!ctx->dll) goto fail;
    LOAD(get_current, CtxGetCurrent, "cuCtxGetCurrent");
    LOAD(push, CtxPushCurrent, "cuCtxPushCurrent_v2");
    LOAD(pop, CtxPopCurrent, "cuCtxPopCurrent_v2");
    LOAD(clear, MemsetD32Async, "cuMemsetD32Async");
    LOAD(launch, LaunchKernel, "cuLaunchKernel");
    LOAD(copy, MemcpyDtoHAsync, "cuMemcpyDtoHAsync_v2");
    LOAD(sync, StreamSynchronize, "cuStreamSynchronize");
    LOAD(record, EventRecord, "cuEventRecord");
    LOAD(elapsed, EventElapsedTime, "cuEventElapsedTime");
    LOAD(error_string, GetErrorString, "cuGetErrorString");
    ctx->context = cuda_context; ctx->stream = stream; ctx->device = result_device;
    ctx->host = result_host; ctx->begin = begin_event; ctx->end = end_event;
    ctx->threads = threads; ctx->per_thread = per_thread; ctx->difficulty = difficulty;
    for (int index = 0; index < 20; ++index) {
        ctx->functions[index] = (void *)functions[index];
        if (!ctx->functions[index]) goto fail;
    }
    if (!SHA256_Init(&ctx->prefix) || !SHA256_Update(&ctx->prefix, message, message_length)) goto fail;
    return ctx;
fail:
    if (ctx->dll) FreeLibrary(ctx->dll);
    free(ctx);
    return NULL;
}

HC_EXPORT int HC_CALL nc_scan(void *user, uint64_t start, uint64_t count,
                              HCScanResult *result, double *kernel_seconds) {
    NCCuda *ctx = user;
    if (!ctx || !result || !kernel_seconds || !count || count > UINT32_MAX || count - 1 > UINT64_MAX - start)
        return -1;
    memset(result, 0, sizeof(*result));
    *kernel_seconds = 0;
    unsigned int digits = 1;
    for (uint64_t n = start; n >= 10; n /= 10) ++digits;
    uint64_t last = start + count - 1;
    unsigned int last_digits = 1;
    for (uint64_t n = last; n >= 10; n /= 10) ++last_digits;
    if (digits != last_digits) return failure(ctx, "decimal boundary", -1);
    int error = 0, pushed = 0;
    void *previous = NULL;
    if ((error = ctx->get_current(&previous))) return failure(ctx, "cuCtxGetCurrent", error);
    if (previous != ctx->context) {
        if ((error = ctx->push(ctx->context))) return failure(ctx, "cuCtxPushCurrent", error);
        pushed = 1;
    }
    uint64_t found = ctx->device, nonce = ctx->device + 8, digest = ctx->device + 16;
    void *args[] = { &start, &count, &found, &nonce, &digest };
    unsigned int grid = (unsigned int)((count + (uint64_t)ctx->threads * ctx->per_thread - 1) /
                                       ((uint64_t)ctx->threads * ctx->per_thread));
#define CUDA_CALL(operation, call) do { if ((error = (call))) { failure(ctx, operation, error); goto done; } } while (0)
    CUDA_CALL("cuMemsetD32Async", ctx->clear(found, 0, 1, ctx->stream));
    CUDA_CALL("cuEventRecord(begin)", ctx->record(ctx->begin, ctx->stream));
    CUDA_CALL("cuLaunchKernel", ctx->launch(ctx->functions[digits - 1], grid, 1, 1,
               ctx->threads, 1, 1, 0, ctx->stream, args, NULL));
    CUDA_CALL("cuEventRecord(end)", ctx->record(ctx->end, ctx->stream));
    CUDA_CALL("cuMemcpyDtoHAsync", ctx->copy(ctx->host, ctx->device, 48, ctx->stream));
    CUDA_CALL("cuStreamSynchronize", ctx->sync(ctx->stream));
    {
        float milliseconds = 0;
        CUDA_CALL("cuEventElapsedTime", ctx->elapsed(&milliseconds, ctx->begin, ctx->end));
        *kernel_seconds = milliseconds / 1000.0;
        const uint32_t *host = ctx->host;
        if (host[0] > 1) { error = failure(ctx, "invalid result flag", -1); goto done; }
        result->checked = count; result->last_nonce = last; result->found = host[0];
        if (result->found) {
            result->nonce = (uint64_t)host[2] | ((uint64_t)host[3] << 32);
            for (unsigned int i = 0; i < 8; ++i) {
                uint32_t word = host[4 + i];
                for (unsigned int b = 0; b < 4; ++b)
                    result->hash[4 * i + b] = (unsigned char)(word >> (24 - 8 * b));
            }
        }
        unsigned char decimal[20], reverse[20];
        size_t size = 0;
        uint64_t n = last;
        do { reverse[size++] = (unsigned char)('0' + n % 10); n /= 10; } while (n);
        for (size_t i = 0; i < size; ++i) decimal[i] = reverse[size - i - 1];
        SHA256_CTX hasher = ctx->prefix;
        if (!SHA256_Update(&hasher, decimal, size) || !SHA256_Final(result->last_hash, &hasher))
            error = failure(ctx, "OpenSSL last-hash", -1);
    }
done:
    /* A failed submission may still own in-flight device writes. Drain before
     * returning its reservation for CPU retry or releasing Python resources. */
    if (error) ctx->sync(ctx->stream);
    if (pushed) {
        void *popped = NULL;
        int pop_error = ctx->pop(&popped);
        if (!error && pop_error) error = failure(ctx, "cuCtxPopCurrent", pop_error);
    }
    return error;
}

HC_EXPORT const char *HC_CALL nc_error_text(void *user) {
    return user ? ((NCCuda *)user)->error : "No native CUDA resources";
}

HC_EXPORT void HC_CALL nc_destroy(void *user) {
    NCCuda *ctx = user;
    if (!ctx) return;
    if (ctx->dll) FreeLibrary(ctx->dll);
    free(ctx);
}
