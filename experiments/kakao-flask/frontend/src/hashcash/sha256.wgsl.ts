/** One-block SHA-256; each invocation hashes a different decimal nonce. */
export const HASHCASH_WGSL = `
struct Params {
  count: u32,
  messageLength: u32,
  digitLength: u32,
  difficulty: u32,
  message: array<u32, 35>,
  digits: array<u32, 20>,
}
struct Result {
  first: atomic<u32>,
  found: array<u32, 8>,
  last: array<u32, 8>,
}
@group(0) @binding(0) var<storage, read> params: Params;
@group(0) @binding(1) var<storage, read_write> result: Result;

const K = array<u32, 64>(
  0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
  0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
  0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
  0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
  0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
  0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
  0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
  0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
  0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
  0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
  0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
  0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
  0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
  0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
  0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
  0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
);
fn rotate(value: u32, count: u32) -> u32 {
  return (value >> count) | (value << (32u - count));
}
fn digestForOffset(offset: u32) -> array<u32, 8> {
  var digits: array<u32, 20>;
  var carry = offset;
  // Host batches never cross a decimal-length boundary, so no leading digit is lost.
  for (var cursor = i32(params.digitLength) - 1; cursor >= 0; cursor--) {
    let sum = params.digits[u32(cursor)] - 48u + carry;
    digits[u32(cursor)] = 48u + sum % 10u;
    carry = sum / 10u;
  }
  var words: array<u32, 64>;
  for (var cursor = 0u; cursor < params.messageLength; cursor++) {
    words[cursor / 4u] |= params.message[cursor] << ((3u - cursor % 4u) * 8u);
  }
  for (var cursor = 0u; cursor < params.digitLength; cursor++) {
    let position = params.messageLength + cursor;
    words[position / 4u] |= digits[cursor] << ((3u - position % 4u) * 8u);
  }
  let length = params.messageLength + params.digitLength;
  words[length / 4u] |= 0x80u << ((3u - length % 4u) * 8u);
  words[15] = length * 8u;
  for (var cursor = 16u; cursor < 64u; cursor++) {
    let first = words[cursor - 15u];
    let second = words[cursor - 2u];
    let small0 = rotate(first, 7u) ^ rotate(first, 18u) ^ (first >> 3u);
    let small1 = rotate(second, 17u) ^ rotate(second, 19u) ^ (second >> 10u);
    words[cursor] = words[cursor - 16u] + small0 + words[cursor - 7u] + small1;
  }
  var a = 0x6a09e667u;
  var b = 0xbb67ae85u;
  var c = 0x3c6ef372u;
  var d = 0xa54ff53au;
  var e = 0x510e527fu;
  var f = 0x9b05688cu;
  var g = 0x1f83d9abu;
  var h = 0x5be0cd19u;
  for (var cursor = 0u; cursor < 64u; cursor++) {
    let big1 = rotate(e, 6u) ^ rotate(e, 11u) ^ rotate(e, 25u);
    let choice = (e & f) ^ ((~e) & g);
    let first = h + big1 + choice + K[cursor] + words[cursor];
    let big0 = rotate(a, 2u) ^ rotate(a, 13u) ^ rotate(a, 22u);
    let majority = (a & b) ^ (a & c) ^ (b & c);
    let second = big0 + majority;
    h = g; g = f; f = e; e = d + first;
    d = c; c = b; b = a; a = first + second;
  }
  return array<u32, 8>(
    a + 0x6a09e667u, b + 0xbb67ae85u, c + 0x3c6ef372u, d + 0xa54ff53au,
    e + 0x510e527fu, f + 0x9b05688cu, g + 0x1f83d9abu, h + 0x5be0cd19u
  );
}
fn meetsTarget(digest: array<u32, 8>) -> bool {
  let fullWords = params.difficulty / 8u;
  for (var cursor = 0u; cursor < fullWords; cursor++) {
    if (digest[cursor] != 0u) { return false; }
  }
  let remaining = params.difficulty % 8u;
  if (remaining != 0u && (digest[fullWords] >> (32u - remaining * 4u)) != 0u) {
    return false;
  }
  return true;
}
@compute @workgroup_size(64)
fn search(@builtin(global_invocation_id) invocation: vec3<u32>) {
  let offset = invocation.x;
  if (offset >= params.count) { return; }
  let digest = digestForOffset(offset);
  if (meetsTarget(digest)) { atomicMin(&result.first, offset); }
  if (offset == params.count - 1u) {
    for (var cursor = 0u; cursor < 8u; cursor++) { result.last[cursor] = digest[cursor]; }
  }
}
// A second dispatch resolves the atomic minimum without a competing-writer race.
@compute @workgroup_size(1)
fn resolve() {
  let offset = atomicLoad(&result.first);
  if (offset < params.count) {
    let digest = digestForOffset(offset);
    for (var cursor = 0u; cursor < 8u; cursor++) { result.found[cursor] = digest[cursor]; }
  }
}
`;
