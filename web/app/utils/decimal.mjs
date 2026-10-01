/** Display-only decimal shifting/rounding. Never recomputes an Agent formula. */
export function decimalText(value, {shift = 0, places = 2, grouping = true} = {}) {
  if (typeof value !== 'string' || !/^[+-]?\d+(?:\.\d+)?$/.test(value)) return '—';
  if (!Number.isInteger(shift) || !Number.isInteger(places) || places < 0 || places > 16 || Math.abs(shift) > 16) throw new RangeError('Unsupported display scale');
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace(/^[+-]/, '').split('.');
  const magnitude = BigInt(whole + fraction);
  const exponent = shift + places - fraction.length;
  let rounded;
  if (exponent >= 0) rounded = magnitude * 10n ** BigInt(exponent);
  else {
    const denominator = 10n ** BigInt(-exponent);
    rounded = magnitude / denominator + (magnitude % denominator * 2n >= denominator ? 1n : 0n);
  }
  const digits = rounded.toString().padStart(places + 1, '0');
  let integer = places ? digits.slice(0, -places) : digits;
  if (grouping) integer = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (negative && rounded !== 0n ? '-' : '') + integer + (places ? '.' + digits.slice(-places) : '');
}

export const invalidLabels = {
  MISSING_INPUT: '缺少输入', ZERO_DENOMINATOR: '分母为零', NEGATIVE_BASE: '负基数，不展示增长率',
  INCOMPARABLE: '不可比较', MISSING: '缺少数值', NOT_APPLICABLE: '不适用', REVIEW_REQUIRED: '需要复核'
};

export function calculationText(calc) {
  if (!calc) return {text: '记录未保存', valid: false};
  if (calc.status !== 'VALID' || calc.value === null) return {text: invalidLabels[calc.status] || '无有效数值', valid: false};
  if (typeof calc.value !== 'string') return {text: '数值格式需要核对', valid: false};
  // RATIO -> percent; PERCENTAGE_POINTS already carries the 100 multiplier.
  const percent = calc.output_unit === 'RATIO';
  const pp = ['PERCENTAGE_POINTS', 'PP', 'PERCENTAGE_POINT'].includes(calc.output_unit);
  const text = decimalText(calc.value, {shift: percent ? 2 : 0});
  return {text: text + (percent ? '%' : pp ? ' 个百分点' : ''), valid: text !== '—'};
}

export function observationText(obs, billions = false) {
  if (!obs) return {text: '记录未保存', valid: false};
  if (obs.value_status !== 'OBSERVED' || obs.standard_value === null) return {text: invalidLabels[obs.value_status] || '无有效数值', valid: false};
  // Only yuan/CNY observations may be displayed in 亿元; keep original in reader.
  if (billions && (obs.standard_unit !== 'CNY_YUAN' || obs.currency !== 'CNY')) return {text: '单位需要核对', valid: false};
  const text = decimalText(obs.standard_value, {shift: billions ? -8 : 0});
  return {text, valid: text !== '—'};
}
