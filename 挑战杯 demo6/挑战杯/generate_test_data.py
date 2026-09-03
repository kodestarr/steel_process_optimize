"""
Synthetic data generator for testing.
Creates realistic steel plate/part datasets with varied characteristics.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import shutil

def generate_dataset(
    output_dir: Path,
    n_plates: int = 109,
    n_segments: int = 4,
    thickness_range: tuple = (6, 40),
    parts_per_plate_range: tuple = (1, 38),
    small_ratio: float = 0.77,  # 700/910
    priority_levels: int = 9,
    seed: int = 42,
):
    """Generate a synthetic steel plate/part dataset."""
    rng = np.random.default_rng(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Generate plates ──
    thicknesses = np.array([6,7,8,9,10,10.5,11,11.5,12,12.5,13,14,14.5,15,15.5,
                           16,16.5,17,18,19,20,21,22,23,24,25,26,28,30,32,34,36,38,40])
    thicknesses = thicknesses[(thicknesses >= thickness_range[0]) & (thicknesses <= thickness_range[1])]

    segments = [f"SEG{i+1:02d}" for i in range(n_segments)]

    plates = []
    for i in range(n_plates):
        seg = rng.choice(segments)
        thick = rng.choice(thicknesses)
        # Plate dimensions scale with thickness
        length = rng.uniform(2000, 12000)
        width = rng.uniform(1500, 3500)
        weight = length * width * thick * 7.85 / 1e6  # kg
        n_parts = int(rng.integers(parts_per_plate_range[0], parts_per_plate_range[1] + 1))
        n_small = int(n_parts * rng.uniform(0.5, 0.95))
        n_large = n_parts - n_small

        # Cut lengths correlate with dimensions and n_parts
        cut_len = rng.uniform(5000, 250000) * (thick / 20)
        v_bevel = rng.uniform(0, 60000) * (thick / 20) if rng.random() > 0.3 else 0
        empty_travel = cut_len * rng.uniform(0.3, 0.8)
        marking = cut_len * rng.uniform(0, 0.3)
        pierce_count = int(n_parts * rng.uniform(0.8, 1.2))

        plates.append({
            '序号': i + 1,
            '分段号': seg,
            '套料图名': f"{seg}-PL{i+1:04d}",
            '零件总数': n_parts,
            '穿孔数': pierce_count,
            '切割长度(mm)': round(cut_len, 1),
            '空行程长度(mm)': round(empty_travel, 1),
            '划线长度(mm)': round(marking, 1),
            '厚度(mm)': thick,
            '钢板长度(mm)': round(length, 0),
            '钢板宽度(mm)': round(width, 0),
            '零件总重(kg)': round(weight * 0.6, 1),
            '钢板总重(kg)': round(weight, 1),
            '小件数量': n_small,
            '大件数量': n_large,
            'V坡长度(mm)': round(v_bevel, 1),
        })

    plates_df = pd.DataFrame(plates)

    # ── 2. Generate parts ──
    parts = []
    part_id = 0
    for _, plate in plates_df.iterrows():
        for j in range(int(plate['零件总数'])):
            part_id += 1
            is_small = j < plate['小件数量']
            part_type = '小件' if is_small else '大件'

            # Part dimensions
            p_len = rng.uniform(200, 8000)
            p_width = rng.uniform(200, 3000)
            p_thick = plate['厚度(mm)'] * rng.uniform(0.9, 1.0)
            p_weight = p_len * p_width * p_thick * 7.85 / 1e9

            cut_len = rng.uniform(1000, 50000)
            v_bevel = rng.uniform(0, 20000) if rng.random() > 0.4 else 0
            y_bevel = rng.uniform(0, 15000) if rng.random() > 0.6 else 0
            k_bevel = rng.uniform(0, 10000) if rng.random() > 0.7 else 0
            i_bevel = rng.uniform(0, 5000) if rng.random() > 0.8 else 0
            x_bevel = rng.uniform(0, 12000) if rng.random() > 0.6 else 0
            grind_len = rng.uniform(0, 15000) if rng.random() > 0.05 else np.nan
            priority = int(rng.integers(1, priority_levels + 1))

            parts.append({
                '序号': part_id,
                '零件名': f"PART-{part_id:05d}",
                '套料图名': plate['套料图名'],
                '零件长度(mm)': round(p_len, 1),
                '零件宽度(mm)': round(p_width, 1),
                '零件厚度(mm)': round(p_thick, 1),
                '零件重量(mm)': round(p_weight, 4),
                '坡口总长(mm)': round(v_bevel + y_bevel + k_bevel + i_bevel + x_bevel, 1),
                '零件类型': part_type,
                '零件材质': 'A',
                '切割长度(mm)': round(cut_len, 1),
                'V坡长度(mm)': round(v_bevel, 1),
                'Y坡长度(mm)': round(y_bevel, 1),
                'K坡长度(mm)': round(k_bevel, 1),
                'I坡长度(mm)': round(i_bevel, 1),
                'X坡长度(mm)': round(x_bevel, 1),
                '齐套优先级': priority,
                '打磨长度(mm)': round(grind_len, 1) if not np.isnan(grind_len) else np.nan,
            })

    parts_df = pd.DataFrame(parts)

    # ── 3. Write to Excel ──
    out_path = output_dir / "附件2：钢板零件数据.xlsx"
    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        plates_df.to_excel(writer, sheet_name='钢板数据', index=False)
        parts_df.to_excel(writer, sheet_name='零件数据', index=False)

    # ── 4. Copy 附件3 (speed table) ──
    src_speed = Path("data/data_0/附件3：工艺用时计算表.xlsx")
    dst_speed = output_dir / "附件3：工艺用时计算表.xlsx"
    shutil.copy2(src_speed, dst_speed)

    # Stats
    seg_dist = plates_df['分段号'].value_counts().to_dict()
    seg_pri_groups = parts_df.groupby(['套料图名', '齐套优先级']).ngroups

    print(f"  {output_dir.name}: {n_plates} plates, {part_id} parts, "
          f"{n_segments} segments, {seg_pri_groups} kit-groups, "
          f"thickness {thickness_range[0]}-{thickness_range[1]}mm")
    return plates_df, parts_df


if __name__ == "__main__":
    base = Path("data")

    datasets = [
        # (dir_name, n_plates, n_segments, thickness_range, parts_range, small_ratio, priorities, seed)
        ("data_1", 55, 3, (6, 30),   (1, 25), 0.75, 6, 101),
        ("data_2", 218, 4, (6, 40),  (1, 40), 0.78, 9, 202),
        ("data_3", 150, 8, (6, 40),  (2, 35), 0.80, 7, 303),
        ("data_4", 80,  4, (20, 50), (3, 30), 0.70, 5, 404),   # thick plates only
        ("data_5", 300, 6, (6, 35),  (5, 50), 0.82, 8, 505),   # large scale
        ("data_6", 109, 2, (6, 25),  (1, 20), 0.85, 4, 606),   # few segments, many plates each
        ("data_7", 109, 10, (8, 38), (2, 15), 0.72, 10, 707),  # many segments
        ("data_8", 40,  4, (6, 40),  (1, 20), 0.80, 6, 808),   # small for quick tests
    ]

    for (name, np_, ns, tr, ppr, sr, pl, s) in datasets:
        generate_dataset(base / name, np_, ns, tr, ppr, sr, pl, s)

    print(f"\nDone! Created {len(datasets)} datasets in {base}/")
