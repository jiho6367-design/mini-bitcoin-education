/* Experimental full native nonce loop. Not used by the production CPU backend.
 * Uses OpenSSL's existing CPU dispatch, plus native decimal increment/state copy.
 * SHA256_CTX APIs are deprecated in OpenSSL 3, but present in the measured local
 * build. This deliberately isolated experiment does not install a dependency.
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <openssl/crypto.h>
#include <openssl/sha.h>

#ifdef _WIN32
#define HC_EXPORT __declspec(dllexport)
#else
#define HC_EXPORT __attribute__((visibility("default")))
#endif
#ifndef HC_SOURCE_SHA256
#define HC_SOURCE_SHA256 "unrecorded"
#endif

typedef struct {
    uint64_t checked;
    uint64_t nonce;
    uint64_t last_nonce;
    uint32_t found;
    uint32_t reserved;
    unsigned char hash[SHA256_DIGEST_LENGTH];
    unsigned char last_hash[SHA256_DIGEST_LENGTH];
} HCResult;

_Static_assert(sizeof(HCResult) == 96, "HCResult ABI changed");

HC_EXPORT unsigned int hc_abi_version(void) { return 1; }
HC_EXPORT const char *hc_source_sha256(void) { return HC_SOURCE_SHA256; }
HC_EXPORT const char *hc_openssl_version(void) { return OpenSSL_version(OPENSSL_VERSION); }
HC_EXPORT const char *hc_cpu_info(void) { return OpenSSL_version(OPENSSL_CPU_INFO); }

static size_t decimal(uint64_t value, unsigned char digits[20]) {
    unsigned char reverse[20];
    size_t length = 0;
    do {
        reverse[length++] = (unsigned char)('0' + value % 10);
        value /= 10;
    } while (value);
    for (size_t i = 0; i < length; ++i) digits[i] = reverse[length - 1 - i];
    return length;
}

static size_t next_decimal(unsigned char digits[20], size_t length) {
    size_t i = length;
    while (i && digits[i - 1] == '9') digits[--i] = '0';
    if (i) {
        ++digits[i - 1];
        return length;
    }
    /* The caller never increments after UINT64_MAX, so length is at most 19
     * here. Crossing 9 -> 10, 99 -> 100 etc. remains within the 20-byte array.
     */
    memmove(digits + 1, digits, length);
    digits[0] = '1';
    return length + 1;
}

static int matches(const unsigned char digest[32], unsigned int difficulty) {
    const unsigned int bytes = difficulty / 2;
    for (unsigned int i = 0; i < bytes; ++i) if (digest[i]) return 0;
    return !(difficulty & 1) || digest[bytes] < 0x10;
}

/* count is a nonzero uint64 count, and the complete inclusive range must fit
 * uint64. search=0 finishes the whole set while retaining the first proof.
 * 0 = success, 1 = bad input, 2 = OpenSSL failure.
 */
HC_EXPORT int hc_scan(const unsigned char *message, size_t message_length,
                      unsigned int difficulty, uint64_t start, uint64_t count,
                      unsigned int search, HCResult *result) {
    if (!result || (!message && message_length) || !count || difficulty < 1 ||
        difficulty > 64 || search > 1 || count - 1 > UINT64_MAX - start) return 1;
    memset(result, 0, sizeof(*result));
    SHA256_CTX prefix;
    if (!SHA256_Init(&prefix) || !SHA256_Update(&prefix, message, message_length)) return 2;
    unsigned char digits[20];
    size_t length = decimal(start, digits);
    uint64_t nonce = start;
    for (uint64_t i = 0; i < count; ++i) {
        SHA256_CTX current = prefix;
        if (!SHA256_Update(&current, digits, length) ||
            !SHA256_Final(result->last_hash, &current)) return 2;
        result->checked = i + 1;
        result->last_nonce = nonce;
        if (matches(result->last_hash, difficulty)) {
            if (!result->found) {
                result->found = 1;
                result->nonce = nonce;
                memcpy(result->hash, result->last_hash, SHA256_DIGEST_LENGTH);
            }
            if (search) return 0;
        }
        if (i + 1 < count) {
            ++nonce;
            length = next_decimal(digits, length);
        }
    }
    return 0;
}
