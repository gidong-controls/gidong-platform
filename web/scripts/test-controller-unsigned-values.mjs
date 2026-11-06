import assert from "node:assert/strict";
import test from "node:test";
import {
  controllerUnsignedTraceSummaries,
  formatControllerUnsignedValue,
  INCOMPLETE_CONTROLLER_TRACE,
} from "../src/controllerUnsignedValues.ts";

// Node 22+: node --experimental-strip-types --test scripts/test-controller-unsigned-values.mjs
test("actual JSON parsing rounds LWORD but raw bytes retain every bit", () => {
  const parsed = JSON.parse('{"unsignedValue":9007199254740993,"rawHex":"0100000000002000"}');
  assert.equal(parsed.unsignedValue, 9007199254740992);
  assert.equal(formatControllerUnsignedValue(parsed.rawHex, "LWORD"), "9007199254740993");
});

test("safe boundaries, unsigned64 maximum and little-endian bytes", () => {
  for (const [raw, expected] of [
    ["FFFFFFFFFFFF1F00", "9007199254740991"],
    ["0000000000002000", "9007199254740992"],
    ["0100000000002000", "9007199254740993"],
    ["FFFFFFFFFFFFFFFF", "18446744073709551615"],
    ["0102030405060708", "578437695752307201"],
  ]) assert.equal(formatControllerUnsignedValue(raw, "LWORD"), expected);
});

test("normal BIT through DWORD display remains the same unsigned decimal", () => {
  for (const [type, raw, expected] of [
    ["BIT", "00", 0], ["BIT", "01", 1], ["BIT", "FF", 255],
    ["BYTE", "7f", 127], ["WORD", "3412", 4660], ["DWORD", "78563412", 305419896],
  ]) assert.equal(formatControllerUnsignedValue(raw, type), String(expected));
});

test("invalid or wrong-width raw bytes never fall back to a rounded number", () => {
  for (const raw of ["", "0", "GG", "0x01", " 01", "0100", null, 1]) {
    assert.equal(formatControllerUnsignedValue(raw, "BYTE"), "확인 불가 (원본 바이트 형식)");
  }
  assert.equal(formatControllerUnsignedValue("01", "LWORD"), "확인 불가 (원본 바이트 형식)");
  assert.equal(formatControllerUnsignedValue("01", "UNKNOWN"), "확인 불가 (원본 바이트 형식)");
});

function trace() {
  return { dataType: "LWORD", variables: ["%ML0", "%ML1"], sampleCount: 2, samples: [
    { index: 0, values: [
      { variable: "%ml0", rawHex: "0100000000002000", unsignedValue: JSON.parse("9007199254740993") },
      { variable: "%ML1", rawHex: "FFFFFFFFFFFFFFFF", unsignedValue: JSON.parse("18446744073709551615") },
    ] },
    { index: 1, values: [
      { variable: "%ML0", rawHex: "0000000000002000", unsignedValue: JSON.parse("9007199254740992") },
      { variable: "%ml1", rawHex: "0100000000000000", unsignedValue: 1 },
    ] },
  ] };
}

test("trace derives exact first/last/min/max even when parsed numbers compare equal", () => {
  const source = trace(), before = structuredClone(source);
  assert.equal(source.samples[0].values[0].unsignedValue, source.samples[1].values[0].unsignedValue);
  const result = controllerUnsignedTraceSummaries(source);
  assert.deepEqual(result.get("%ML0"), { first: "9007199254740993", last: "9007199254740992",
    minimum: "9007199254740992", maximum: "9007199254740993" });
  assert.deepEqual(result.get("%ML1"), { first: "18446744073709551615", last: "1",
    minimum: "1", maximum: "18446744073709551615" });
  assert.deepEqual(source, before);
});

test("missing, reordered or identity-mismatched samples are explicitly unavailable", () => {
  for (const mutate of [
    (value) => value.samples.pop(),
    (value) => { value.samples[1].index = 7; },
    (value) => { value.samples[1].values.pop(); },
    (value) => { value.samples[1].values[1].variable = "%ML0"; },
  ]) {
    const source = trace(); mutate(source);
    assert.deepEqual(controllerUnsignedTraceSummaries(source).get("%ML0"), {
      first: INCOMPLETE_CONTROLLER_TRACE, last: INCOMPLETE_CONTROLLER_TRACE,
      minimum: INCOMPLETE_CONTROLLER_TRACE, maximum: INCOMPLETE_CONTROLLER_TRACE });
  }
});

test("one invalid variable's raw bytes do not corrupt another variable's summary", () => {
  const source = trace(); source.samples[0].values[0].rawHex = "invalid";
  const result = controllerUnsignedTraceSummaries(source);
  assert.equal(result.get("%ML0").maximum, "확인 불가 (원본 바이트 형식)");
  assert.equal(result.get("%ML1").maximum, "18446744073709551615");
});
