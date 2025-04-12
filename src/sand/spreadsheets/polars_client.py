import polars as pl
from tkinter import filedialog, messagebox
import logging

logger = logging.getLogger(__name__)

class FileOpener:

    def __init__(self):
        self.file = self._open_file()
        self.df = 0

    def _open_file(self) -> None:
        """
        This fuction opens the file dialog to pick a file.

        Raise:
            Exception: If you do not pick a file it raises an Exception
        """
        try:
            while True:
                filename = filedialog.askopenfilename()
                if filename:
                    return filename
                else:
                    messagebox.showerror("Error", "Please select a file.")
        except Exception as e:
            logger.error(f"Error: {e}")

    def _reading(self, file_dict: str) -> None:
        try:
            if filename:
                self.extension = filename.split('.')[-1]

                if self.extension == 'csv':
                    df = pl.read_csv(filename.split('.')[0])
                    return df
                elif self.extension in ('xlsx', 'xls'):
                    df = pl.read_excel(filename.split('.')[0])
                    return df
                else:
                    messagebox.showerror(f'Please select a csv, xls or xlsx file, current extension selected {self.extension}')
        except Exception as e:
            logger.error(f'Please select a file, error: {e}')

def main() -> None:
    fl = FileOpener()
    filename = fl._open_file
    fl._reading(filename)


if __name__ == "__main__":
    main()
