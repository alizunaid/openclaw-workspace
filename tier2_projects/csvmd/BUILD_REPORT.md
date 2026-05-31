# Build report: csvmd

Last build run wall-clock: 134.3s

## Subsystems

| # | subsystem | status | ocb run_id | built_at | files |
|---|-----------|--------|------------|----------|-------|
| 1 | `file-reader` | done | run_1780117492 | 2026-05-30T05:08:35Z | file_reader.py, main.py, smoke_test.py |
| 2 | `csv-parser` | done | run_1780184292 | 2026-05-30T23:43:19Z | csv_parser.py, test_csv_parser.py, utils.py |
| 3 | `markdown-formatter` | done | run_1780262881 | 2026-05-31T21:32:48Z | main.py, markdown_formatter.py, test_main.py |
| 4 | `file-writer` | done | run_1780263191 | 2026-05-31T21:35:25Z | file_writer.py, main.py, smoke_test.py |

## Integration smoke

**Verdict: PASS** — chain composes end-to-end on disk (file-reader:read_csv_file -> csv-parser:parse_csv -> markdown-formatter:rows_to_markdown -> file-writer:write_markdown_to_file); all 7 cells intact incl. quoted-comma row

Resolved entry points (topological order):

- `file-reader` -> `read_csv_file`
- `csv-parser` -> `parse_csv`
- `markdown-formatter` -> `rows_to_markdown`
- `file-writer` -> `write_markdown_to_file`
