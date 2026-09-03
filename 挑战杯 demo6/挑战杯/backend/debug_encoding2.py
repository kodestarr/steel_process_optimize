"""Debug _safe_filename function - writes results to file to avoid GBK stdout issues"""
import sys, json
from pathlib import Path
sys.path.insert(0, '.')

PROJECT_ROOT = Path(__file__).parent.parent

original = "附件2：钢板零件数据.xlsx"
utf8_bytes = original.encode('utf-8')
garbled = utf8_bytes.decode('latin-1')

results = {"original": original, "garbled": garbled, "utf8_bytes_hex": utf8_bytes.hex()}

# Latin-1 recovery
recovered_latin1 = garbled.encode('latin-1').decode('utf-8')
results["latin1_recovery"] = recovered_latin1
results["latin1_recovery_match"] = recovered_latin1 == original

# Test _safe_filename
from main import _safe_filename
sfn_garbled = _safe_filename(garbled)
sfn_original = _safe_filename(original)
results["safe_fn_garbled_input"] = sfn_garbled
results["safe_fn_garbled_fixed"] = sfn_garbled == original
results["safe_fn_original_input"] = sfn_original
results["safe_fn_original_unchanged"] = sfn_original == original

with open(PROJECT_ROOT / "encoding_debug_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("Results written to encoding_debug_result.json")
