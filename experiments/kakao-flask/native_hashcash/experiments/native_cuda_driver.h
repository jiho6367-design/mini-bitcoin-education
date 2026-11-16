#ifndef HASHCASH_NATIVE_CUDA_DRIVER_H
#define HASHCASH_NATIVE_CUDA_DRIVER_H
#include "native_scheduler.h"

/* Resources are borrowed from a live, independently self-tested CuPy backend.
 * The wrapper retains CUfunction/module/stream/event/pinned-buffer ownership.
 * No Python callback executes during nc_scan or the native scheduler run. */
HC_EXPORT void *HC_CALL nc_create(void *cuda_context, void *stream,
    const uintptr_t functions[20], uint64_t result_device, void *result_host,
    void *begin_event, void *end_event, uint32_t threads, uint32_t per_thread,
    const unsigned char *message, size_t message_length, uint32_t difficulty);
HC_EXPORT int HC_CALL nc_scan(void *user, uint64_t start, uint64_t count,
                              HCScanResult *result, double *kernel_seconds);
HC_EXPORT const char *HC_CALL nc_error_text(void *user);
HC_EXPORT void HC_CALL nc_destroy(void *user);
#endif
