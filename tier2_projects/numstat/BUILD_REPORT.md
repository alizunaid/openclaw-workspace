# Build report: numstat

Last build run wall-clock: 278.7s

## Subsystems

| # | subsystem | status | ocb run_id | built_at | files |
|---|-----------|--------|------------|----------|-------|
| 1 | `file-reader` | done | run_1780267172 | 2026-05-31T22:42:35Z | file_reader.py, test_file_reader.py |
| 2 | `histogram-builder` | done | run_1780336318 | 2026-06-01T17:53:32Z | histogram_builder.py, histogram_utils.py |
| 3 | `statistics-computer` | done | run_1780267823 | 2026-05-31T22:53:17Z | smoke_test_statistics_computer.py, statistics_computer.py |
| 4 | `report-generator` | done | run_1780336412 | 2026-06-01T17:55:37Z | formatter.py, report_generator.py |
| 5 | `file-writer` | done | run_1780336537 | 2026-06-01T17:56:36Z | build_subsystem_file_writer_purpose_writ.py |

## Integration smoke

**Verdict: FAIL** — import of file-writer failed

Findings:

- file-writer: import failed — FileNotFoundError: no canonical entry module file_writer.py in /root/.openclaw/workspace/tier2_projects/numstat/subsystems/file-writer (expected `file-writer` -> file_writer.py)
