"""Russian-language Windows setup wizard."""

import os
import queue
import re
import subprocess
import threading
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from archive_patch import GAME_NAME, InstallationError, archive_in, install

TITLE = "Установщик русификатора USC: Counterforce"
REPOSITORY = "https://github.com/Doroko-Dorokovich/Rusificator_USCCounterforce"
ASSETS = Path(__file__).resolve().parent
DUMP = Path(__file__).resolve().with_name('I2Languages_RU.txt')
if not DUMP.is_file():
    DUMP = DUMP.parent.parent / 'I2Languages_RU.txt'


def vdf_strings(path, field):
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return []
    pattern = rf'"{re.escape(field)}"\s*"((?:[^"\\]|\\.)*)"'
    return [value.replace(r"\\", "\\") for value in re.findall(pattern, content, flags=re.IGNORECASE)]


def steam_roots():
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, path, name in keys:
        try:
            with winreg.OpenKey(hive, path) as key:
                value, _ = winreg.QueryValueEx(key, name)
            if value:
                yield Path(value)
        except OSError:
            continue
    yield Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"


def default_game_path():
    roots = list(dict.fromkeys(steam_roots()))
    libraries = []
    for root in roots:
        paths = vdf_strings(root / "steamapps" / "libraryfolders.vdf", "path")
        libraries.extend([root, *(Path(path) for path in paths)])
    for library in dict.fromkeys(libraries):
        apps = library / "steamapps"
        names = vdf_strings(apps / "appmanifest_1574870.acf", "installdir")
        for name in dict.fromkeys([*names, GAME_NAME]):
            if not name or Path(name).name != name or name in (".", ".."):
                continue
            folder = apps / "common" / name
            try:
                archive_in(folder)
            except (InstallationError, OSError, UnicodeError):
                continue
            return folder.resolve()
    return roots[0] / "steamapps" / "common" / GAME_NAME


class SetupWizard:
    def __init__(self, window):
        self.window = window
        window.title(TITLE)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.cancel)

        self.events = queue.Queue()
        self.busy = False
        self.game_path = tk.StringVar(value=os.path.normpath(str(default_game_path())))
        self.backup = tk.BooleanVar(value=False)
        self.open_repository = tk.BooleanVar(value=True)
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_status = tk.StringVar()
        self.progress_percent = tk.StringVar(value="0 %")

        self.art = tk.PhotoImage(file=ASSETS / "welcome-art.png")
        self.badge = tk.PhotoImage(file=ASSETS / "wizard-badge.png")
        window.iconbitmap(default=str(ASSETS / "setup.ico"))
        window.option_add("*Font", ("Segoe UI", 9))

        self.page = tk.Frame(window, width=496, height=314, background="#FFFFFF")
        self.page.pack(fill="both")
        self.page.pack_propagate(False)
        ttk.Separator(window).pack(fill="x")
        footer = ttk.Frame(window, padding=(12, 0), width=496, height=45)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.cancel_button = ttk.Button(footer, text="Отмена", command=self.cancel, width=11)
        self.cancel_button.pack(side="right")
        self.next_button = ttk.Button(footer, width=12, default="active")
        self.next_button.pack(side="right", padx=(5, 8))
        self.back_button = ttk.Button(footer, text="< Назад", width=11, command=self.show_welcome)
        self.show_welcome()
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_reqheight()) // 2)
        window.geometry(f"+{x}+{y}")

    def clear_page(self):
        for widget in self.page.winfo_children():
            widget.destroy()

    def reset_body(self, heading, caption=""):
        self.clear_page()
        header = tk.Frame(self.page, background="#FFFFFF", height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, image=self.badge, background="#FFFFFF").pack(side="right", padx=10)
        text = tk.Frame(header, background="#FFFFFF")
        text.pack(fill="both", padx=(21, 0), pady=(7, 2))
        tk.Label(text, text=heading, font=("Segoe UI", 9, "bold"),
                 background="#FFFFFF", anchor="w").pack(fill="x")
        if caption:
            tk.Label(text, text=caption, background="#FFFFFF", anchor="w",
                     justify="left", wraplength=370, padx=0).pack(fill="x", padx=(12, 0))
        ttk.Separator(self.page).pack(fill="x")
        self.body = ttk.Frame(self.page, padding=(36, 15, 36, 11))
        self.body.pack(fill="both", expand=True)

    def show_welcome(self):
        self.clear_page()
        tk.Label(self.page, image=self.art, borderwidth=0, highlightthickness=0).pack(side="left")
        panel = tk.Frame(self.page, background="#FFFFFF", padx=12, pady=14)
        panel.pack(side="left", fill="both", expand=True)
        tk.Label(panel, text="Вас приветствует Установщик русификатора от D_O_R_O_K_O!",
                 font=("Segoe UI", 12, "bold"), background="#FFFFFF",
                 justify="left", anchor="nw", wraplength=302, padx=0).pack(fill="x")
        for label in (
            "Мастер установит русификатор USC: Counterforce на ваш компьютер.",
            "Перед установкой закройте игру.",
            "Нажмите «Далее», чтобы выбрать папку игры.",
        ):
            tk.Label(panel, text=label, background="#FFFFFF", justify="left",
                     anchor="w", wraplength=294, padx=0).pack(fill="x", pady=(12, 0))
        self.back_button.pack_forget()
        self.cancel_button.configure(state="normal")
        self.next_button.configure(text="Далее >", command=self.show_location, state="normal")
        self.next_button.focus_set()
        self.window.bind("<Return>", lambda _: self.show_location())

    def show_location(self):
        self.reset_body("Выбор папки установки", "Укажите, где установлена USC: Counterforce.")
        ttk.Label(self.body, text="Русификатор будет установлен в указанную папку.").pack(anchor="w")
        ttk.Label(self.body, text="Чтобы выбрать другую папку игры, нажмите «Обзор».").pack(anchor="w", pady=(11, 0))
        location = ttk.Frame(self.body)
        location.pack(fill="x", pady=(7, 14))
        field = ttk.Entry(location, textvariable=self.game_path)
        field.pack(side="left", fill="x", expand=True)
        ttk.Button(location, text="Обзор…", command=self.browse, width=10).pack(side="right", padx=(10, 0))
        ttk.Checkbutton(self.body, text="Создать бэкап файлов игры", variable=self.backup).pack(anchor="w")
        ttk.Label(self.body, text="Для установки требуется около 3,6 ГБ свободного места.").pack(side="bottom", anchor="w")
        if not self.back_button.winfo_manager():
            self.back_button.pack(side="right")
        self.next_button.configure(text="Установить", command=self.start, state="normal")
        self.cancel_button.configure(state="normal")
        field.focus_set()
        field.xview_moveto(1)
        self.window.bind("<Return>", lambda _: self.start())

    def browse(self):
        current = Path(self.game_path.get())
        folder = filedialog.askdirectory(
            parent=self.window, title="Выберите папку USC: Counterforce",
            initialdir=current if current.is_dir() else current.parent, mustexist=True,
        )
        if folder:
            self.game_path.set(os.path.normpath(folder))

    def start(self):
        if self.busy:
            return
        selected = self.game_path.get().strip().strip('"')
        if not selected:
            messagebox.showerror(TITLE, "Укажите папку игры.", parent=self.window)
            return
        folder = Path(selected)
        make_backup = self.backup.get()
        self.busy = True
        self.reset_body("Установка русификатора", "Подождите, пока мастер обновит файлы игры.")
        self.back_button.pack_forget()
        self.progress_value.set(0)
        self.progress_status.set("Подготавливаем установку…")
        self.progress_percent.set("0 %")
        ttk.Label(self.body, textvariable=self.progress_status, wraplength=540).pack(anchor="w")
        ttk.Progressbar(self.body, variable=self.progress_value, maximum=100).pack(fill="x", pady=(16, 8))
        ttk.Label(self.body, textvariable=self.progress_percent, foreground="#40576D").pack(anchor="e")
        self.next_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.window.unbind("<Return>")
        threading.Thread(target=self.worker, args=(folder, make_backup), daemon=True).start()
        self.window.after(70, self.poll)

    def worker(self, folder, make_backup):
        def report(progress, message):
            self.events.put(("progress", progress, message))

        try:
            changed, backup = install(folder, DUMP, make_backup, report)
        except InstallationError as error:
            self.events.put(("error", str(error)))
        except (PermissionError, OSError) as error:
            if getattr(error, "winerror", None) in (5, 32, 33, 1224) or isinstance(error, PermissionError):
                self.events.put(("error", "Не удалось получить доступ к архиву. Закройте игру. Если папка защищена Windows, запустите Setup.exe от имени администратора."))
            else:
                self.events.put(("error", f"Ошибка чтения или записи игрового архива.\n\n{error}"))
        except subprocess.CalledProcessError:
            self.events.put(("error", "Не удалось проверить, закрыта ли игра."))
        except Exception as error:
            self.events.put(("error", f"Не удалось обработать игровой архив.\n\n{error}"))
        else:
            self.events.put(("done", changed, backup))

    def poll(self):
        while not self.events.empty():
            event, *args = self.events.get_nowait()
            if event == "progress":
                progress, text = args
                self.progress_value.set(progress)
                self.progress_status.set(text)
                self.progress_percent.set(f"{progress} %")
            elif event == "error":
                self.busy = False
                self.show_location()
                messagebox.showerror(TITLE, args[0], parent=self.window)
                return
            else:
                self.busy = False
                self.show_finish(*args)
                return
        self.window.after(70, self.poll)

    def show_finish(self, changed, backup):
        self.reset_body("Установка завершена", "Благодарим за установку русификатора USC: Counterforce!")
        message = "Перевод установлен. В игре выберите язык «magyar»." if changed else "Эта версия русификатора уже установлена."
        ttk.Label(self.body, text=message).pack(anchor="w")
        if backup:
            ttk.Label(self.body, text=f"Создана резервная копия {backup.name}",
                      wraplength=535).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(self.body, text="Зайти на репозиторий русификатора",
                        variable=self.open_repository).pack(side="bottom", anchor="w", pady=(0, 8))
        self.next_button.configure(text="Готово", command=self.finish, state="normal")
        self.cancel_button.pack_forget()
        self.next_button.focus_set()
        self.window.bind("<Return>", lambda _: self.finish())

    def finish(self):
        if self.open_repository.get():
            try:
                os.startfile(REPOSITORY)
            except OSError:
                messagebox.showerror(TITLE, "Не удалось открыть репозиторий в браузере.", parent=self.window)
        self.window.destroy()

    def cancel(self):
        if not self.busy:
            self.window.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    SetupWizard(root)
    root.mainloop()
