import type { LSControllerDataType, LSControllerTrace } from "./api/types";

const BYTE_WIDTHS: Record<LSControllerDataType, number> = { BIT: 1, BYTE: 1, WORD: 2, DWORD: 4, LWORD: 8 };
const INVALID_BYTES = "확인 불가 (원본 바이트 형식)";
export const INCOMPLETE_CONTROLLER_TRACE = "확인 불가 (전체 표본 누락)";

function unsignedFromLittleEndianBytes(rawHex: unknown, dataType: LSControllerDataType): bigint | null {
  const width = BYTE_WIDTHS[dataType];
  if (typeof width !== "number" || typeof rawHex !== "string" ||
      rawHex.length !== width * 2 || !/^[0-9a-f]+$/i.test(rawHex)) return null;
  let value = 0n;
  for (let end = rawHex.length; end > 0; end -= 2) {
    value = (value << 8n) | BigInt(`0x${rawHex.slice(end - 2, end)}`);
  }
  return value;
}

/** JSON numbers can lose LWORD bits; display the original FEnet bytes instead. */
export function formatControllerUnsignedValue(rawHex: unknown, dataType: LSControllerDataType): string {
  return unsignedFromLittleEndianBytes(rawHex, dataType)?.toString() ?? INVALID_BYTES;
}

interface UnsignedTraceDisplay {
  first: string;
  last: string;
  minimum: string;
  maximum: string;
}

function unavailableSummary(reason: string): UnsignedTraceDisplay {
  return { first: reason, last: reason, minimum: reason, maximum: reason };
}

/** Derive display strings once from the complete, ordered Host trace samples. */
export function controllerUnsignedTraceSummaries(
  trace: Pick<LSControllerTrace, "dataType" | "variables" | "sampleCount" | "samples">,
): ReadonlyMap<string, UnsignedTraceDisplay> {
  const variables = trace.variables.map((variable) => variable.toUpperCase());
  const unavailable = () => new Map(variables.map((variable) =>
    [variable, unavailableSummary(INCOMPLETE_CONTROLLER_TRACE)] as const));
  // These count, order and variable-identity checks mirror LSControllerTraceV1.
  if (!Number.isInteger(trace.sampleCount) || trace.sampleCount < 2 || trace.sampleCount > 64 ||
      variables.length < 1 || variables.length > 16 || new Set(variables).size !== variables.length ||
      !Array.isArray(trace.samples) || trace.samples.length !== trace.sampleCount) return unavailable();
  const values = new Map<string, { first: bigint; last: bigint; minimum: bigint; maximum: bigint }>();
  const invalidBytes = new Set<string>();
  for (const [index, sample] of trace.samples.entries()) {
    if (sample.index !== index || !Array.isArray(sample.values) || sample.values.length !== variables.length ||
        sample.values.some((value, position) => value.variable.toUpperCase() !== variables[position])) {
      return unavailable();
    }
    for (const sampleValue of sample.values) {
      const variable = sampleValue.variable.toUpperCase();
      const value = unsignedFromLittleEndianBytes(sampleValue.rawHex, trace.dataType);
      if (value === null) { invalidBytes.add(variable); continue; }
      const current = values.get(variable);
      values.set(variable, current ? { first: current.first, last: value,
        minimum: value < current.minimum ? value : current.minimum,
        maximum: value > current.maximum ? value : current.maximum }
        : { first: value, last: value, minimum: value, maximum: value });
    }
  }
  return new Map(variables.map((variable) => {
    const current = values.get(variable);
    return [variable, invalidBytes.has(variable) ? unavailableSummary(INVALID_BYTES)
      : current ? { first: current.first.toString(), last: current.last.toString(),
          minimum: current.minimum.toString(), maximum: current.maximum.toString() }
        : unavailableSummary(INCOMPLETE_CONTROLLER_TRACE)] as const;
  }));
}
