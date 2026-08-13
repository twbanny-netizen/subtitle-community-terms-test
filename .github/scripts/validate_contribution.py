"""
Validation script for community contribution PRs.
Called by GitHub Actions to automatically validate contributions.
Only validates the changes in the current PR, not the entire file.
Immediately removes polluted entries from pending_candidates.json.
"""
import json
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime
import os

# Fix Windows console encoding if running locally
if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Configuration
CONSENSUS_THRESHOLD = 3  # Number of different contributors required
POLLUTION_PATTERNS = [
    r"〔示範翻譯〕",
    r"根據下列英文義項",
    r"詞：",
    r"\[翻譯錯誤\]",
    r"^\s*$",  # Empty string
    r"參見",
    r"條",
    r"《",
    r"》",
    r"參考",
    r"例：",
    r"例如",
    r"典出",
    r"語出",
]

# File paths
OFFICIAL_DICT_PATH = Path("official_dictionary.json")
PENDING_CANDIDATES_PATH = Path("pending_candidates.json")
REJECTED_LOG_PATH = Path("rejected_log.json")

# GitHub environment variables
PR_NUMBER = os.environ.get("PR_NUMBER")
GITHUB_REF = os.environ.get("GITHUB_REF", "")

def load_json_file(path):
    """Load JSON file safely."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8-sig'))
        return {}
    except Exception as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return None

def save_json_file(path, data):
    """Save JSON file safely."""
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8-sig')
        return True
    except Exception as e:
        print(f"Error saving {path}: {e}", file=sys.stderr)
        return False

def get_pr_changes():
    """
    Get the keys that were changed in this PR.
    Returns a set of keys that were added or modified.
    """
    try:
        # Get the diff between base branch and PR branch
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Only process if pending_candidates.json was changed
        if "pending_candidates.json" not in result.stdout:
            print("No changes to pending_candidates.json in this PR")
            return set()
        
        # Get the actual diff for pending_candidates.json
        result = subprocess.run(
            ["git", "diff", "origin/main...HEAD", "pending_candidates.json"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse the diff to find added/modified keys
        changed_keys = set()
        lines = result.stdout.split('\n')
        
        for line in lines:
            # Look for added lines (starting with +) that contain key patterns
            if line.startswith('+') and not line.startswith('++') and '"' in line:
                # Extract key from JSON line
                match = re.search(r'"([^"]+)\|', line)
                if match:
                    changed_keys.add(match.group(1))
        
        print(f"Detected {len(changed_keys)} changed keys in this PR")
        return changed_keys
        
    except Exception as e:
        print(f"Error getting PR changes: {e}", file=sys.stderr)
        # Fallback: check all keys if we can't determine the diff
        return None

def validate_entry_format(key, value):
    """Validate the format of a single entry."""
    # Key should be in format "term|translation"
    if "|" not in key:
        return False, f"Invalid key format: {key}"
    
    parts = key.split("|")
    if len(parts) != 2:
        return False, f"Invalid key format: {key}"
    
    term, translation = parts
    if not term or not translation:
        return False, f"Empty term or translation in key: {key}"
    
    # Value should have required fields
    if not isinstance(value, dict):
        return False, f"Invalid value type for key {key}"
    
    required_fields = ["contributors", "first_seen", "last_updated"]
    for field in required_fields:
        if field not in value:
            return False, f"Missing field {field} in key {key}"
    
    if not isinstance(value["contributors"], list):
        return False, f"contributors must be a list for key {key}"
    
    return True, "Format validation passed"

def check_pollution(text):
    """Check if text contains pollution patterns."""
    if not text:
        return True
    
    # Check against known pollution patterns
    for pattern in POLLUTION_PATTERNS:
        if re.search(pattern, text):
            return True
    
    # Check for excessively long translations (likely dictionary entries)
    if len(text) > 80:
        return True
    
    # Check for multiple periods (likely dictionary definitions)
    if text.count("。") >= 2:
        return True
    
    return False

def validate_entry_pollution(key, value):
    """Check for pollution in a single entry."""
    parts = key.split("|")
    if len(parts) != 2:
        return False, "Invalid key format"
    
    term, translation = parts
    
    # Check term for pollution
    if check_pollution(term):
        return False, f"Term '{term}' contains pollution markers"
    
    # Check translation for pollution
    if check_pollution(translation):
        return False, f"Translation '{translation}' contains pollution markers"
    
    return True, "Pollution check passed"

def validate_entry_charset(key, value):
    """Validate character set for a single entry (Japanese -> Chinese)."""
    parts = key.split("|")
    if len(parts) != 2:
        return False, "Invalid key format"
    
    term, translation = parts
    
    # Check that translation is primarily Chinese characters
    # Basic heuristic: should contain mostly CJK characters
    chinese_chars = sum(1 for c in translation if '\u4e00' <= c <= '\u9fff')
    total_chars = len(translation.strip())
    
    if total_chars > 0 and chinese_chars / total_chars < 0.5:
        return False, f"Translation '{translation}' doesn't appear to be primarily Chinese"
    
    return True, "Charset validation passed"

def check_consensus(pending_data, official_data):
    """
    Check which entries have reached consensus threshold.
    Returns (entries_to_promote, entries_still_pending).
    """
    entries_to_promote = {}
    entries_still_pending = {}
    
    for key, value in pending_data.items():
        parts = key.split("|")
        if len(parts) != 2:
            continue
        
        term, translation = parts
        contributor_count = len(set(value["contributors"]))  # Unique contributors
        
        if contributor_count >= CONSENSUS_THRESHOLD:
            # Check if not already in official dictionary
            if term not in official_data:
                entries_to_promote[term] = translation
            else:
                # Already in official, just remove from pending
                entries_still_pending[key] = value
        else:
            entries_still_pending[key] = value
    
    return entries_to_promote, entries_still_pending

def log_rejection(contributor_uuid, term, translation, reason):
    """Log a rejected submission to rejected_log.json."""
    rejected_log = load_json_file(REJECTED_LOG_PATH) or []
    
    rejection_entry = {
        "timestamp": datetime.now().isoformat(),
        "contributor_uuid": contributor_uuid,
        "term": term,
        "translation": translation,
        "reason": reason
    }
    
    rejected_log.append(rejection_entry)
    save_json_file(REJECTED_LOG_PATH, rejected_log)

def set_github_output(name, value):
    """Set GitHub Actions output."""
    if os.environ.get("GITHUB_ACTIONS"):
        with open(os.environ.get("GITHUB_OUTPUT"), "a") as fh:
            print(f"{name}={value}", file=fh)
    else:
        # For local testing
        print(f"{name}={value}")

def main():
    """Main validation logic - only validates PR changes and removes polluted entries immediately."""
    # Load data
    pending_data = load_json_file(PENDING_CANDIDATES_PATH)
    official_data = load_json_file(OFFICIAL_DICT_PATH) or {}
    
    if pending_data is None:
        set_github_output("result", "failure")
        set_github_output("message", "Failed to load pending_candidates.json")
        return 1
    
    # Get the keys that were changed in this PR
    changed_keys = get_pr_changes()
    
    # If we can't determine changes, validate all entries (fallback)
    if changed_keys is None:
        print("Warning: Could not determine PR changes, validating all entries")
        keys_to_validate = list(pending_data.keys())
    elif len(changed_keys) == 0:
        print("No changes to validate in this PR")
        print("result=success")
        print("message=No changes to pending_candidates.json")
        return 0
    else:
        keys_to_validate = list(changed_keys)
    
    print(f"Validating {len(keys_to_validate)} changed entries")
    
    # Track validation results
    valid_entries = []
    rejected_entries = []
    polluted_keys_to_remove = []
    
    # Validate each changed entry individually
    for key in keys_to_validate:
        if key not in pending_data:
            print(f"Warning: Key {key} not found in pending data, skipping")
            continue
        
        value = pending_data[key]
        parts = key.split("|")
        if len(parts) != 2:
            print(f"Warning: Invalid key format {key}, skipping")
            continue
        
        term, translation = parts
        
        # Step 1: Format validation
        format_valid, format_message = validate_entry_format(key, value)
        if not format_valid:
            print(f"Format validation failed for {key}: {format_message}")
            rejected_entries.append((key, value, f"format_invalid: {format_message}"))
            continue
        
        # Step 2: Pollution validation
        pollution_valid, pollution_message = validate_entry_pollution(key, value)
        if not pollution_valid:
            print(f"Pollution validation failed for {key}: {pollution_message}")
            rejected_entries.append((key, value, f"pollution_detected: {pollution_message}"))
            polluted_keys_to_remove.append(key)
            
            # Log rejection immediately
            contributors = value.get("contributors", ["unknown"])
            for contributor in contributors:
                log_rejection(contributor, term, translation, f"pollution_detected: {pollution_message}")
            continue
        
        # Step 3: Charset validation
        charset_valid, charset_message = validate_entry_charset(key, value)
        if not charset_valid:
            print(f"Charset validation failed for {key}: {charset_message}")
            rejected_entries.append((key, value, f"charset_invalid: {charset_message}"))
            continue
        
        # Entry passed all validations
        valid_entries.append(key)
    
    # Remove polluted entries from pending_candidates.json immediately
    if polluted_keys_to_remove:
        print(f"Removing {len(polluted_keys_to_remove)} polluted entries from pending_candidates.json")
        for key in polluted_keys_to_remove:
            if key in pending_data:
                del pending_data[key]
        save_json_file(PENDING_CANDIDATES_PATH, pending_data)
    
    # If all changed entries were rejected, fail the PR
    if len(valid_entries) == 0 and len(rejected_entries) > 0:
        set_github_output("result", "failure")
        set_github_output("message", f"All {len(rejected_entries)} changed entries failed validation")
        return 1
    
    # If some entries were rejected but others are valid, proceed with valid ones
    if len(rejected_entries) > 0:
        print(f"Warning: {len(rejected_entries)} entries were rejected and removed, but {len(valid_entries)} valid entries remain")
    
    # Step 4: Consensus checking (only for valid entries)
    entries_to_promote = {}
    entries_still_pending = {}
    
    for key in valid_entries:
        if key not in pending_data:
            continue
        
        value = pending_data[key]
        parts = key.split("|")
        if len(parts) != 2:
            continue
        
        term, translation = parts
        contributor_count = len(set(value["contributors"]))  # Unique contributors
        
        if contributor_count >= CONSENSUS_THRESHOLD:
            # Check if not already in official dictionary
            if term not in official_data:
                entries_to_promote[term] = translation
            else:
                # Already in official, just remove from pending
                entries_still_pending[key] = value
        else:
            entries_still_pending[key] = value
    
    # Update official dictionary if entries reached consensus
    if entries_to_promote:
        official_data.update(entries_to_promote)
        save_json_file(OFFICIAL_DICT_PATH, official_data)
        print(f"Promoted {len(entries_to_promote)} entries to official dictionary")
    
    # Update pending candidates (remove promoted entries)
    save_json_file(PENDING_CANDIDATES_PATH, entries_still_pending)
    
    # Prepare result message
    if entries_to_promote:
        message = f"Validation passed. Promoted {len(entries_to_promote)} entries to official dictionary. {len(entries_still_pending)} entries remain pending."
    else:
        message = f"Validation passed. {len(entries_still_pending)} entries remain pending (consensus threshold: {CONSENSUS_THRESHOLD})"
    
    if len(rejected_entries) > 0:
        message += f" {len(rejected_entries)} entries were rejected and removed."
    
    set_github_output("result", "success")
    set_github_output("message", message)
    return 0

if __name__ == "__main__":
    sys.exit(main())