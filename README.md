# Community Dictionary Contribution System

## Overview
This repository manages community-contributed dictionary terms through an automated filtering system.

## Files
- `official_dictionary.json` - Dictionary terms that have passed consensus validation (downloaded by users)
- `pending_candidates.json` - Terms submitted by contributors awaiting consensus threshold
- `rejected_log.json` - Log of automatically rejected submissions with reasons

## Schema

### official_dictionary.json
```json
{
  "japanese_term": "chinese_translation"
}
```

### pending_candidates.json
```json
{
  "japanese_term|chinese_translation": {
    "contributors": ["uuid1", "uuid2", "uuid3"],
    "first_seen": "ISO8601_timestamp",
    "last_updated": "ISO8601_timestamp"
  }
}
```

### rejected_log.json
```json
[
  {
    "timestamp": "ISO8601_timestamp",
    "contributor_uuid": "uuid",
    "term": "japanese_term",
    "translation": "chinese_translation",
    "reason": "pollution_detected|format_invalid|charset_invalid"
  }
]
```

## Consensus Rules
- A term needs 3 different contributor UUIDs to be promoted to official_dictionary.json
- Same UUID submitting the same term+translation multiple times counts as 1 contribution
- Automatic validation checks format, pollution patterns, and character set validity