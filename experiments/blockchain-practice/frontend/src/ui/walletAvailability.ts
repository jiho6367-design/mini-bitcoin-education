import type { NodeSnapshot } from "../api/types";

export interface WalletAvailability {
  known: boolean;
  confirmed: number | null;
  spendable: number | null;
  reserved: number | null;
  reservedInputs: number;
  immature: number | null;
}

/** Reservation is the wallet's actual locked inputs, not every unavailable coin. */
export function walletAvailability(node: NodeSnapshot | undefined, address: string): WalletAvailability {
  const balance = node?.balances[address];
  if (!node || !balance || !Number.isFinite(balance.confirmed) || !Number.isFinite(balance.spendable)) {
    return { known: false, confirmed: null, spendable: null, reserved: null, reservedInputs: 0, immature: null };
  }
  const keys = new Set(node.reserved);
  const inputs = Object.entries(node.utxos).filter(([key, output]) =>
    output.address === address && keys.has(key));
  return {
    known: true,
    confirmed: balance.confirmed,
    spendable: balance.spendable,
    reserved: inputs.reduce((sum, [, output]) => sum + output.amount, 0),
    reservedInputs: inputs.length,
    immature: balance.immature ?? 0,
  };
}
