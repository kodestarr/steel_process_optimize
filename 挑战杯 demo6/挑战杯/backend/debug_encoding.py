"""Debug _safe_filename function"""
import sys
sys.path.insert(0, '.')

# Reconstruct the garbled string from Latin-1 encoding of UTF-8
# Original: 附件2：钢板零件数据.xlsx
original = "附件2：钢板零件数据.xlsx"
utf8_bytes = original.encode('utf-8')
garbled = utf8_bytes.decode('latin-1')

print(f"Original:  {original}")
print(f"Garbled:   {garbled}")
print(f"Length: in={len(garbled)}, orig={len(original)}")

# Test recovery
recovered_latin1 = garbled.encode('latin-1').decode('utf-8')
print(f"Latin1->UTF8: {recovered_latin1}")
print(f"Match: {recovered_latin1 == original}")

# Test CJK detection
has_cjk = any('一' <= c <= '鿿' for c in recovered_latin1)
print(f"Has CJK: {has_cjk}")

# Test with _safe_filename
from main import _safe_filename
result = _safe_filename(garbled)
print(f"_safe_filename result: {result}")
print(f"Fixed: {result == original}")

# Test with already-correct filename
result2 = _safe_filename(original)
print(f"Already-correct result: {result2}")
print(f"Unchanged: {result2 == original}")
