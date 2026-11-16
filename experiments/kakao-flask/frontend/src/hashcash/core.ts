/** Original hashcash-only protocol: SHA256(UTF8(message) || ASCII(decimal_nonce)). */
export const MAX_NONCE = (1n << 64n) - 1n;
const encoder = new TextEncoder();
const MAX_MESSAGE_BYTES = 4096;

export type HashcashSettings = {
  message: string;
  difficulty: number;
  startNonce: string;
};

export type HashcashProgress = {
  type: "progress" | "found" | "exhausted" | "error";
  checked: string;
  nonce: string | null;
  hash: string | null;
  elapsedMs: number;
  error?: string;
};

export type HashcashWorkerRequest = {
  type: "start";
  runId: string;
  settings: HashcashSettings;
};

export type HashcashWorkerResponse = HashcashProgress & { runId: string };

function messageBytes(message: unknown): Uint8Array<ArrayBuffer> {
  if (typeof message !== "string") throw new Error("메시지를 입력하세요");
  // TextEncoder would silently replace lone surrogates, unlike the source's UTF-8 encoder.
  for (const character of message) {
    const unit = character.charCodeAt(0);
    if (character.length === 1 && unit >= 0xd800 && unit <= 0xdfff) {
      throw new Error("메시지에 올바르지 않은 문자가 있습니다");
    }
  }
  const encoded = encoder.encode(message);
  if (encoded.length > MAX_MESSAGE_BYTES) throw new Error("메시지는 UTF-8 4096바이트까지 입력하세요");
  return encoded;
}

function nonceText(value: unknown): string {
  if (typeof value !== "string" || !/^[0-9]+$/.test(value)) {
    throw new Error("시작 넌스는 0 이상의 숫자로 입력하세요");
  }
  const normalized = value.replace(/^0+(?=[0-9])/, "");
  if (normalized.length > 20 || BigInt(normalized) > MAX_NONCE) {
    throw new Error("넌스는 18446744073709551615 이하로 입력하세요");
  }
  return normalized;
}

export function validateSettings(value: unknown): HashcashSettings {
  if (!value || typeof value !== "object") throw new Error("탐색 조건을 확인하세요");
  const settings = value as Partial<HashcashSettings>;
  messageBytes(settings.message);
  if (typeof settings.difficulty !== "number" || !Number.isInteger(settings.difficulty)
      || settings.difficulty < 1 || settings.difficulty > 64) {
    throw new Error("난이도는 1~64 사이의 정수로 입력하세요");
  }
  return {
    message: settings.message as string,
    difficulty: settings.difficulty,
    startNonce: nonceText(settings.startNonce),
  };
}

function cryptoDigest(bytes: Uint8Array<ArrayBuffer>): Promise<ArrayBuffer> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("이 브라우저에서는 해시 계산을 사용할 수 없습니다 HTTPS 주소로 접속하세요");
  }
  return globalThis.crypto.subtle.digest("SHA-256", bytes);
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function proofHash(message: string, nonce: string): Promise<string> {
  // Rebuild the complete input independently of the scanner's prepared prefix bytes.
  messageBytes(message);
  return hex(await cryptoDigest(encoder.encode(message + nonceText(nonce))));
}

export async function scanHashcash(
  rawSettings: unknown,
  onProgress: (progress: HashcashProgress) => void = () => undefined,
): Promise<HashcashProgress> {
  const settings = validateSettings(rawSettings);
  const prefix = messageBytes(settings.message);
  const target = "0".repeat(settings.difficulty);
  const started = performance.now();
  let lastReport = started;
  let checked = 0n;
  let nonce = BigInt(settings.startNonce);
  let lastNonce: string | null = null;
  let lastHash: string | null = null;
  const progress = (type: HashcashProgress["type"]): HashcashProgress => ({
    type, checked: checked.toString(), nonce: lastNonce, hash: lastHash,
    elapsedMs: Math.max(0, performance.now() - started),
  });

  try {
    while (nonce <= MAX_NONCE) {
      const decimal = nonce.toString();
      const suffix = encoder.encode(decimal);
      const input = new Uint8Array(prefix.length + suffix.length);
      input.set(prefix);
      input.set(suffix, prefix.length);
      const hash = hex(await cryptoDigest(input));
      checked += 1n;
      lastNonce = decimal;
      lastHash = hash;

      if (hash.startsWith(target)) {
        const verified = await proofHash(settings.message, decimal);
        if (verified !== hash || !verified.startsWith(target)) {
          throw new Error("찾은 넌스의 해시 검증에 실패했습니다 다시 시작하세요");
        }
        return progress("found");
      }

      const now = performance.now();
      if (now - lastReport >= 100) {
        onProgress(progress("progress"));
        lastReport = now;
      }
      // BigInt addition must not wrap uint64 and revisit nonce zero.
      nonce += 1n;
    }
    return progress("exhausted");
  } catch (error: unknown) {
    return {
      ...progress("error"),
      error: error instanceof Error ? error.message : "해시를 계산하지 못했습니다 다시 시작하세요",
    };
  }
}
