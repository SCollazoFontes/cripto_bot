"""File manager for CSV and JSON output."""

from __future__ import annotations

import csv
import pathlib


class FileManager:
    """Manages file I/O for trading data."""

    def __init__(self, run_dir: pathlib.Path):
        """
        Initialize file manager.

        Args:
            run_dir: Directory for output files
        """
        self.run_dir = run_dir
        self.data_csv_path = run_dir / "data.csv"
        self._data_csv_initialized = False

    def ensure_data_csv_header(self) -> None:
        """Initialize data.csv with header if not exists."""
        if not self._data_csv_initialized:
            try:
                if not self.data_csv_path.exists():
                    with self.data_csv_path.open("w", newline="") as f:
                        writer = csv.DictWriter(
                            f,
                            fieldnames=[
                                "timestamp",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "trade_count",
                                "dollar_value",
                                "start_time",
                                "end_time",
                                "duration_ms",
                            ],
                        )
                        writer.writeheader()
                self._data_csv_initialized = True
            except Exception as e:
                print(f"⚠️  Error inicializando data.csv: {e}")

    def append_bar(self, bar_dict: dict) -> None:
        """
        Append a bar to data.csv.

        Args:
            bar_dict: Bar data dictionary
        """
        try:
            self.ensure_data_csv_header()
            with self.data_csv_path.open("a", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "trade_count",
                        "dollar_value",
                        "start_time",
                        "end_time",
                        "duration_ms",
                    ],
                )
                writer.writerow(bar_dict)
        except Exception as e:
            # Don't stop execution for file write issues
            print(f"⚠️  No se pudo escribir en data.csv: {e}")

    def get_bar_rows(self, bar_dicts: list[dict]) -> list[dict]:
        """
        Get list of bar rows (for in-memory storage).

        Args:
            bar_dicts: List of bar dictionaries

        Returns:
            Same list (pass-through for compatibility)
        """
        return bar_dicts
