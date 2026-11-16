/* Experimental Windows-only native scheduler. All nonce ranges are inclusive
 * start + count - 1 and must fit uint64. No Python callback is required. */
#ifndef HASHCASH_NATIVE_SCHEDULER_H
#define HASHCASH_NATIVE_SCHEDULER_H

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define HC_EXPORT __declspec(dllexport)
#define HC_CALL __cdecl
#else
#define HC_EXPORT
#define HC_CALL
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint64_t checked, nonce, last_nonce;
    uint32_t found, reserved;
    unsigned char hash[32], last_hash[32];
} HCScanResult;

typedef int (HC_CALL *HCGpuScan)(void *user, uint64_t start, uint64_t count,
                                HCScanResult *result, double *kernel_seconds);

/* kind: 1 CPU, 2 GPU. status: 0 accepted, other = rejected/failed callback.
 * begin/end are absolute QPC seconds (same clock as Python perf_counter).
 * A failed GPU trace has checked=0; its full range is retried by native CPU.
 * trace capacity retains the first entries and counts omitted later entries. */
typedef struct {
    uint64_t start, count, checked;
    double begin, end, kernel_seconds;
    uint32_t worker, kind, status, found;
    uint64_t nonce;
    unsigned char hash[32], last_hash[32];
} HCTrace;

typedef struct {
    uint32_t abi_version, status, running, cancelled;
    uint32_t found, gpu_failed, trace_count, trace_dropped;
    uint32_t workers, workers_ready, search, reserved;
    uint64_t checked, cpu_checked, gpu_checked, nonce, last_nonce;
    uint64_t assigned, range_start, range_count, cpu_batches, gpu_batches;
    uint64_t retries;
    double begin, end, elapsed, first_proof_seconds;
    double cpu_seconds, gpu_seconds, gpu_kernel_seconds, qpc_frequency;
    unsigned char hash[32], last_hash[32];
    char error[192];
} HCStats;

typedef struct HCCoordinator HCCoordinator;

/* Returns NULL for invalid input/allocation/thread/OpenSSL failures.
 * workers=0 is the C GPU-only control; workers <= 64. create waits for all
 * persistent CPU threads to reach their native start gate. */
HC_EXPORT HCCoordinator *HC_CALL hc_create(const unsigned char *message,
    size_t message_length, uint32_t difficulty, uint32_t workers,
    uint64_t cpu_batch, uint64_t gpu_batch, uint32_t trace_capacity);

/* reset retains already created workers. search=0 completes the entire fixed
 * range while keeping its first proof. search=1 stops new assignment after the
 * first valid proof and drains work already assigned. finite-only tail_guard
 * prevents NEW CPU reservations once unassigned work <= guard; GPU consumes it.
 * It is ignored without a working GPU. Returns 1 for bad input/busy context. */
HC_EXPORT int HC_CALL hc_reset(HCCoordinator *ctx, uint64_t start,
    uint64_t count, uint32_t search, uint64_t tail_guard);

/* Runs GPU work in the calling native thread and CPU work in native workers.
 * callback=NULL permits CPU-only runs if workers>0. Returns 0 on successful
 * completion or cancellation, otherwise 1 input/busy, 2 fatal scan/validation.
 * A GPU failure disables GPU and returns the failed range to CPU, if available.
 * A previously valid proof is preserved even after a later cleanup error. */
HC_EXPORT int HC_CALL hc_run(HCCoordinator *ctx, HCGpuScan callback, void *user);
HC_EXPORT void HC_CALL hc_cancel(HCCoordinator *ctx);
HC_EXPORT int HC_CALL hc_snapshot(HCCoordinator *ctx, HCStats *result);
HC_EXPORT uint32_t HC_CALL hc_copy_traces(HCCoordinator *ctx, HCTrace *out,
                                        uint32_t capacity);
HC_EXPORT void HC_CALL hc_destroy(HCCoordinator *ctx);
HC_EXPORT uint32_t HC_CALL hc_scheduler_abi(void);
HC_EXPORT const char *HC_CALL hc_scheduler_openssl_version(void);
HC_EXPORT const char *HC_CALL hc_scheduler_cpu_info(void);

#ifdef __cplusplus
}
#endif
#endif
