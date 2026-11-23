const sheetMaterial = /mdf|hdf|plywood|particle|melamine|ไม้อัด|ปาร์ติเกิล|เมลามีน|\bpb\b/iu;
const valid = value => Number.isFinite(value) && value > 0 && value <= 1e7;
const sides = value => [0, 1, 2].includes(value);
const sheetArea = 1220 * 2440 / 1e6;

export function estimateMaterials(input, waste) {
  if (!Number.isFinite(waste) || waste < 0 || waste > 50) {
    throw new Error('เปอร์เซ็นต์เผื่อสูญเสียต้องอยู่ระหว่าง 0–50');
  }
  const warnings = [];
  const groups = new Map();
  const parts = input.map((part, index) => {
    const result = {...part, area_m2: null, edge_m: null};
    const {width_mm: width, length_mm: length, quantity, material, thickness_mm: thickness} = part;
    const name = part.name || `รายการ ${index + 1}`;
    if (![width, length, quantity].every(valid) || !Number.isInteger(quantity) || width > length) {
      warnings.push(`${name}: ตรวจขนาดด้านสั้น ด้านยาว และจำนวนก่อนรวมยอด`);
      return result;
    }
    result.area_m2 = width * length * quantity / 1e6;
    if (sides(part.long_edges) && sides(part.short_edges)) {
      result.edge_m = quantity * (part.long_edges * length + part.short_edges * width) / 1000;
    } else {
      warnings.push(`${name}: ยังไม่ทราบจำนวนด้านปิดขอบ`);
    }
    if (!valid(thickness) || !sheetMaterial.test(material)) {
      warnings.push(`${name}: ไม่รวมยอดแผ่นเนื่องจากวัสดุไม่ใช่ไม้แผ่นหรือยังไม่ทราบความหนา`);
      return result;
    }
    const key = JSON.stringify([material, thickness]);
    if (!groups.has(key)) {
      groups.set(key, {material, thickness_mm: thickness, net_area_m2: 0, edge_m: 0, edge_unknown: false, has_oversize: false});
    }
    const group = groups.get(key);
    group.net_area_m2 += result.area_m2;
    group.edge_m += result.edge_m ?? 0;
    group.edge_unknown ||= result.edge_m === null;
    if (width > 1220 || length > 2440) {
      group.has_oversize = true;
      warnings.push(`${name}: ชิ้นงานเกินแผ่น 1220 × 2440 มม.`);
    }
    return result;
  });
  const materials = [...groups.values()].map(group => {
    const gross = group.net_area_m2 * (1 + waste / 100);
    return {...group, gross_area_m2: gross, area_based_sheets: Math.ceil(Number((gross / sheetArea).toFixed(10))), orderable_sheets: null};
  });
  return {parts, materials, warnings, waste_percent: waste, sheet_mm: [1220, 2440]};
}
