import { secp256k1 } from "@noble/curves/secp256k1.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex, hexToBytes } from "@noble/hashes/utils.js";
import type { Transaction, UnsignedTransaction, Wallet } from "./types";

export { bytesToHex, hexToBytes };
export const hashText = (text: string): string =>
  bytesToHex(sha256(new TextEncoder().encode(text)));
export const addressOf = (publicKey: string): string =>
  `mini_${bytesToHex(sha256(hexToBytes(publicKey)))}`;
export const isAddress = (value: unknown): value is string =>
  typeof value === "string" && /^mini_[0-9a-f]{64}$/.test(value);
export const isHash = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

export function createWallet(name: string, secret?: string): Wallet {
  const key = secret ? hexToBytes(secret) : secp256k1.keygen().secretKey;
  const publicKey = bytesToHex(secp256k1.getPublicKey(key));
  return {
    name,
    privateKey: bytesToHex(key),
    publicKey,
    address: addressOf(publicKey),
  };
}

// 배열의 필드 순서를 고정한다. JS 객체의 키 삽입 순서에 서명을 의존하지 않는다.
export function signingText(tx: UnsignedTransaction): string {
  return JSON.stringify([
    tx.version,
    tx.sessionId,
    tx.inputs.map((i) => [i.txId, i.outputIndex]),
    tx.outputs.map((o) => [o.address, o.amount]),
    tx.publicKey,
  ]);
}
export const transactionId = (tx: Omit<Transaction, "id">): string =>
  hashText(JSON.stringify([signingText(tx), tx.signature]));
export function signTransaction(
  tx: UnsignedTransaction,
  privateKey: string,
): Transaction {
  // 이미 SHA-256 처리한 메시지이므로 라이브러리의 추가 prehash는 끈다.
  const signature = bytesToHex(
    secp256k1.sign(
      hexToBytes(hashText(signingText(tx))),
      hexToBytes(privateKey),
      { prehash: false },
    ),
  );
  const signed = { ...structuredClone(tx), signature };
  return { ...signed, id: transactionId(signed) };
}
export function verifySignature(tx: Transaction): boolean {
  try {
    return secp256k1.verify(
      hexToBytes(tx.signature),
      hexToBytes(hashText(signingText(tx))),
      hexToBytes(tx.publicKey),
      { prehash: false },
    );
  } catch {
    return false;
  }
}
