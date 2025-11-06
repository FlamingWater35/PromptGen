import sys
import customtkinter as ctk
from tkinter import filedialog
from CTkMessagebox import CTkMessagebox
from CTkListbox import CTkListbox
from ctkcomponents.ctk_components import CTkProgressPopup
import os
import pyperclip
from pathlib import Path
import logging
import fnmatch
import queue
import concurrent.futures
import configparser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def resource_path(relative_path):
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(os.path.abspath("."))

    full_path = Path(os.path.join(base_path, relative_path))

    if not full_path.exists():
        logging.error(f"Resource not found: '{full_path}'")
        return None

    return str(full_path)


FALLBACK_IGNORE_DIRS = {
    "__pycache__",
    "venv",
    "node_modules",
    "build",
    "dist",
    "*.egg-info",
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".DS_Store",
}
FALLBACK_IGNORE_FILES = {
    ".DS_Store",
    "*.pyc",
    "*.log",
    "*.swp",
    "*.swo",
    "*.tmp",
    "*.bak",
    "*.patch",
    "*.diff",
    "*.orig",
}
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024


def _is_custom_ignored(
    item_path: Path, project_root_path: Path | None, custom_patterns: list[str]
) -> bool:
    if not custom_patterns or not project_root_path:
        return False

    item_name = item_path.name
    relative_item_path_str = None
    try:
        abs_item_path = item_path.resolve(strict=False)
        abs_project_root_path = project_root_path.resolve(strict=False)

        if abs_item_path.is_relative_to(abs_project_root_path):
            relative_item_path_str = str(
                abs_item_path.relative_to(abs_project_root_path)
            )
    except (ValueError, OSError):
        pass

    for pattern_str in custom_patterns:
        pattern = pattern_str.strip()
        if not pattern:
            continue

        if fnmatch.fnmatch(item_name, pattern):
            return True

        if relative_item_path_str:
            norm_rel_path = relative_item_path_str.replace(os.sep, "/")
            norm_pattern = pattern.replace(os.sep, "/")

            if norm_pattern.endswith("/"):
                if item_path.is_dir() and fnmatch.fnmatch(
                    norm_rel_path + "/", norm_pattern
                ):
                    return True
            elif fnmatch.fnmatch(norm_rel_path, norm_pattern):
                return True
    return False


def build_file_tree_string(
    folder_path: Path,
    indent="",
    tree_lines=None,
    gitignore_matcher=None,
    use_gitignore_flag=True,
    custom_ignore_patterns=None,
    project_root_path_for_custom=None,
):
    if tree_lines is None:
        tree_lines = []

    visible_items_data = []

    try:
        entries = list(folder_path.iterdir())
    except PermissionError:
        tree_lines.append(f"{indent}[ACCESS DENIED] {folder_path.name}")
        return "\n".join(tree_lines) if not indent else None
    except FileNotFoundError:
        tree_lines.append(f"{indent}[NOT FOUND] {folder_path.name}")
        return "\n".join(tree_lines) if not indent else None
    except OSError as e:
        tree_lines.append(f"{indent}[ERROR ITERATING] {folder_path.name}: {e}")
        return "\n".join(tree_lines) if not indent else None

    for item_path_obj in entries:
        item_name = item_path_obj.name
        try:
            is_dir = item_path_obj.is_dir()
        except OSError:
            continue

        if (
            use_gitignore_flag
            and gitignore_matcher
            and gitignore_matcher(item_path_obj)
        ):
            continue
        if _is_custom_ignored(
            item_path_obj, project_root_path_for_custom, custom_ignore_patterns
        ):
            continue

        if is_dir:
            if (
                item_name in FALLBACK_IGNORE_DIRS
                or any(
                    fnmatch.fnmatch(item_name, pat)
                    for pat in FALLBACK_IGNORE_DIRS
                    if "*" in pat
                )
                or (item_name.startswith(".") and item_name not in {".well-known"})
            ):
                continue
        else:
            if (
                item_name in FALLBACK_IGNORE_FILES
                or any(
                    fnmatch.fnmatch(item_name, pat)
                    for pat in FALLBACK_IGNORE_FILES
                    if "*" in pat
                )
                or (
                    item_name.startswith(".")
                    and item_name not in {".gitignore", ".gitattributes", ".gitmodules"}
                )
            ):
                continue

        visible_items_data.append((item_name, item_path_obj, is_dir))

    visible_items_data.sort(key=lambda x: (not x[2], x[0].lower()))

    for i, (item_name, item_path_obj, is_dir_val) in enumerate(visible_items_data):
        is_last = i == len(visible_items_data) - 1
        connector = "└── " if is_last else "├── "
        if is_dir_val:
            tree_lines.append(f"{indent}{connector}{item_name}/")
            new_indent = indent + ("    " if is_last else "│   ")
            build_file_tree_string(
                item_path_obj,
                new_indent,
                tree_lines,
                gitignore_matcher,
                use_gitignore_flag,
                custom_ignore_patterns,
                project_root_path_for_custom,
            )
        else:
            tree_lines.append(f"{indent}{connector}{item_name}")

    if not indent:
        return "\n".join(tree_lines)
    return None


def read_file_content(file_path_str: str) -> str:
    file_path = Path(file_path_str)
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return f"[File too large (>{MAX_FILE_SIZE_BYTES//1024//1024}MB): {file_path.name}]\n"
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        return f"[Error reading file {file_path.name}: {e}]\n"


class LLMPromptApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        logging.info("Application starting...")
        self.title("LLM Prompt Generator")
        window_width = 1200
        window_height = 900

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.geometry(f"{window_width}x{window_height}+{x}+{y}")

        icon_path_str = resource_path("docs/icon.ico")
        if icon_path_str:
            self.icon_path = icon_path_str
            self.iconbitmap(self.icon_path, default=self.icon_path)
        else:
            self.icon_path = None

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.project_folder_path: Path | None = None
        self.main_file_paths: list[str] = []
        self.gitignore_matcher = lambda path_to_check: False

        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=os.cpu_count() or 1
        )
        self.ui_queue = queue.Queue()
        self.active_background_tasks = 0
        self._controls_to_disable_while_loading = []
        self.progress_popup = None

        self.custom_ignore_debounce_timer = None
        self.instructions_debounce_timer = None

        self.config_dir = Path.home() / "Documents" / "PromptGenConfigs"
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Configuration directory: {self.config_dir}")
        except Exception as e:
            logging.error(f"Could not create config directory {self.config_dir}: {e}")
        self.config_toplevel = None

        self.file_tree_expanded = False
        self.prompt_expanded = False
        self.default_row_weights = {
            0: 0,
            1: 0,
            2: 3,
            3: 1,
            4: 0,
            5: 2,
        }

        self._setup_ui()
        self._store_original_grid_configs()
        self._setup_expand_buttons()
        self._apply_layout_changes()

        self._load_gitignore()
        self._process_ui_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.project_location_label.after_idle(
            lambda: self._on_project_label_configure(None)
        )

    def _on_closing(self):
        logging.info("Application closing...")
        if self.custom_ignore_debounce_timer:
            self.after_cancel(self.custom_ignore_debounce_timer)
        if self.instructions_debounce_timer:
            self.after_cancel(self.instructions_debounce_timer)
        if self.progress_popup:
            try:
                self.progress_popup.cancel_task()
            except Exception:
                pass
            self.progress_popup = None
        if self.config_toplevel and self.config_toplevel.winfo_exists():
            self._close_config_manager()
        if self.executor:
            logging.debug("Shutting down thread pool executor...")
            self.executor.shutdown(wait=True, cancel_futures=True)
            logging.debug("Thread pool executor shut down.")
        self.destroy()

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(0, weight=self.default_row_weights.get(0, 0))
        self.grid_rowconfigure(1, weight=self.default_row_weights.get(1, 0))
        self.grid_rowconfigure(2, weight=self.default_row_weights.get(2, 0))
        self.grid_rowconfigure(3, weight=self.default_row_weights.get(3, 0))
        self.grid_rowconfigure(4, weight=self.default_row_weights.get(4, 0))
        self.grid_rowconfigure(5, weight=self.default_row_weights.get(5, 0))

        self.top_controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_controls_frame.grid(
            row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="ew"
        )
        self.top_controls_frame.grid_columnconfigure(0, weight=1)
        self.open_project_button = ctk.CTkButton(
            self.top_controls_frame,
            text="Open Project Folder",
            command=self.open_project_folder,
        )
        self.open_project_button.grid(
            row=0, column=0, padx=(0, 10), pady=0, sticky="ew"
        )
        self.use_gitignore_var = ctk.BooleanVar(value=True)
        self.use_gitignore_checkbox = ctk.CTkCheckBox(
            self.top_controls_frame,
            text="Use .gitignore",
            variable=self.use_gitignore_var,
            command=self._debounced_refresh_all_views_and_prompt,
        )
        self.use_gitignore_checkbox.grid(
            row=0, column=1, padx=(0, 0), pady=0, sticky="e"
        )

        self.file_tree_frame = ctk.CTkFrame(self)
        self.file_tree_frame.grid_rowconfigure(1, weight=1)
        self.file_tree_frame.grid_columnconfigure(0, weight=1)
        self.file_tree_frame.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(
            self.file_tree_frame,
            text="Project File Tree:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.file_tree_textbox = ctk.CTkTextbox(
            self.file_tree_frame, wrap="none", state="disabled"
        )
        self.file_tree_textbox.grid(
            row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew"
        )

        self.custom_ignore_frame = ctk.CTkFrame(self)
        self.custom_ignore_frame.grid_rowconfigure(1, weight=1)
        self.custom_ignore_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.custom_ignore_frame,
            text="Custom Ignore Patterns:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.custom_ignore_textbox = ctk.CTkTextbox(
            self.custom_ignore_frame, wrap="word", height=100
        )
        self.custom_ignore_textbox.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.custom_ignore_textbox.insert(
            "0.0", "*.tmp\ncache/\n.DS_Store\n*.bak\n.env"
        )
        self.custom_ignore_textbox.bind("<KeyRelease>", self._on_custom_ignore_typed)

        self.right_pane = ctk.CTkFrame(self)
        self.right_pane.grid_columnconfigure(0, weight=1)
        self.right_pane.grid_rowconfigure(1, weight=0)
        self.right_pane.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            self.right_pane,
            text="Instructions for LLM:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.instructions_textbox = ctk.CTkTextbox(
            self.right_pane, wrap="word", height=150
        )
        self.instructions_textbox.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.instructions_textbox.insert("0.0", "Improve my code by...")
        self.instructions_textbox.bind("<KeyRelease>", self._on_instructions_typed)

        ctk.CTkLabel(
            self.right_pane, text="Main Files:", font=ctk.CTkFont(weight="bold")
        ).grid(row=2, column=0, padx=5, pady=(10, 0), sticky="w")

        self.project_location_label = ctk.CTkLabel(
            self.right_pane, text="Project: Not selected", anchor="w"
        )
        self.project_location_label.grid(
            row=3, column=0, padx=5, pady=(2, 5), sticky="ew"
        )
        self.project_location_label.bind(
            "<Configure>", self._on_project_label_configure
        )

        self.main_files_listbox = CTkListbox(self.right_pane, multiple_selection=True)
        self.main_files_listbox.grid(row=4, column=0, padx=5, pady=5, sticky="nsew")

        self.main_files_action_buttons_frame = ctk.CTkFrame(
            self.right_pane, fg_color="transparent"
        )
        self.main_files_action_buttons_frame.grid(
            row=5, column=0, padx=5, pady=(0, 5), sticky="ew"
        )
        self.main_files_action_buttons_frame.grid_columnconfigure(0, weight=1)
        self.main_files_action_buttons_frame.grid_columnconfigure(1, weight=1)
        self.main_files_action_buttons_frame.grid_columnconfigure(2, weight=1)
        self.main_files_action_buttons_frame.grid_columnconfigure(3, weight=1)
        self.add_folder_files_button = ctk.CTkButton(
            self.main_files_action_buttons_frame,
            text="Add Folder",
            command=self.add_files_from_folder,
        )
        self.add_folder_files_button.grid(
            row=0, column=0, padx=(0, 2), pady=5, sticky="ew"
        )
        self.add_folder_recursively_button = ctk.CTkButton(
            self.main_files_action_buttons_frame,
            text="Add Folder Recursively",
            command=self.add_files_from_folder_recursively,
        )
        self.add_folder_recursively_button.grid(
            row=0, column=1, padx=2, pady=5, sticky="ew"
        )
        self.add_individual_files_button = ctk.CTkButton(
            self.main_files_action_buttons_frame,
            text="Add File(s)",
            command=self.add_individual_files,
        )
        self.add_individual_files_button.grid(
            row=0, column=2, padx=2, pady=5, sticky="ew"
        )
        self.unselect_files_button = ctk.CTkButton(
            self.main_files_action_buttons_frame,
            text="Remove File(s)",
            command=self.unselect_main_files,
        )
        self.unselect_files_button.grid(
            row=0, column=3, padx=(2, 0), pady=5, sticky="ew"
        )

        self.final_prompt_frame = ctk.CTkFrame(self)
        self.final_prompt_frame.grid_rowconfigure(1, weight=1)
        self.final_prompt_frame.grid_columnconfigure(0, weight=1)
        self.final_prompt_frame.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(
            self.final_prompt_frame,
            text="Final Prompt (auto-updates):",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.final_prompt_textbox = ctk.CTkTextbox(
            self.final_prompt_frame, wrap="word", state="disabled"
        )
        self.final_prompt_textbox.grid(
            row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew"
        )
        self.final_prompt_buttons_frame = ctk.CTkFrame(
            self.final_prompt_frame, fg_color="transparent"
        )
        self.final_prompt_buttons_frame.grid(
            row=2, column=0, columnspan=2, padx=5, pady=5, sticky="e"
        )
        self.manage_configs_button = ctk.CTkButton(
            self.final_prompt_buttons_frame,
            text="Manage Configs",
            command=self._open_config_manager,
        )
        self.manage_configs_button.pack(side="left", padx=(0, 10), pady=(0, 5))
        self.copy_prompt_button = ctk.CTkButton(
            self.final_prompt_buttons_frame,
            text="Copy Prompt",
            command=self.copy_prompt,
        )
        self.copy_prompt_button.pack(side="left", padx=(0, 5), pady=(0, 5))

        self._controls_to_disable_while_loading = [
            self.open_project_button,
            self.use_gitignore_checkbox,
            self.custom_ignore_textbox,
            self.instructions_textbox,
            self.add_folder_files_button,
            self.add_folder_recursively_button,
            self.add_individual_files_button,
            self.unselect_files_button,
            self.main_files_listbox,
            self.manage_configs_button,
            self.copy_prompt_button,
        ]
        if hasattr(self, "expand_file_tree_button"):
            self._controls_to_disable_while_loading.append(self.expand_file_tree_button)
        if hasattr(self, "expand_prompt_button"):
            self._controls_to_disable_while_loading.append(self.expand_prompt_button)

    def _store_original_grid_configs(self):
        self.file_tree_frame_orig_grid = {
            "row": 2,
            "column": 0,
            "padx": (10, 5),
            "pady": 5,
            "sticky": "nsew",
            "rowspan": 1,
            "columnspan": 1,
        }
        self.custom_ignore_frame_orig_grid = {
            "row": 3,
            "column": 0,
            "padx": (10, 5),
            "pady": 5,
            "sticky": "nsew",
            "rowspan": 1,
            "columnspan": 1,
        }
        self.right_pane_orig_grid = {
            "row": 2,
            "column": 1,
            "rowspan": 2,
            "padx": (5, 10),
            "pady": 5,
            "sticky": "nsew",
            "columnspan": 1,
        }
        self.final_prompt_frame_orig_grid = {
            "row": 5,
            "column": 0,
            "columnspan": 2,
            "padx": 10,
            "pady": (5, 10),
            "sticky": "nsew",
            "rowspan": 1,
        }

    def _setup_expand_buttons(self):
        self.expand_file_tree_button = ctk.CTkButton(
            self.file_tree_frame,
            text="↗",
            width=28,
            height=28,
            command=self._toggle_expand_file_tree,
        )
        self.expand_file_tree_button.grid(
            row=0, column=1, padx=5, pady=(5, 0), sticky="ne"
        )

        self.expand_prompt_button = ctk.CTkButton(
            self.final_prompt_frame,
            text="↗",
            width=28,
            height=28,
            command=self._toggle_expand_prompt,
        )
        self.expand_prompt_button.grid(
            row=0, column=1, padx=5, pady=(5, 0), sticky="ne"
        )

    def _toggle_expand_file_tree(self):
        if self.prompt_expanded:
            CTkMessagebox(
                master=self,
                title="Info",
                message="Collapse the Final Prompt section first to expand File Tree.",
                icon="info",
            )
            return
        self.file_tree_expanded = not self.file_tree_expanded
        self._apply_layout_changes()

    def _toggle_expand_prompt(self):
        if self.file_tree_expanded:
            CTkMessagebox(
                master=self,
                title="Info",
                message="Collapse the File Tree section first to expand Final Prompt.",
                icon="info",
            )
            return
        self.prompt_expanded = not self.prompt_expanded
        self._apply_layout_changes()

    def _apply_layout_changes(self):
        self.file_tree_frame.grid_forget()
        self.custom_ignore_frame.grid_forget()
        self.right_pane.grid_forget()
        self.final_prompt_frame.grid_forget()

        for i in range(2, 6):
            self.grid_rowconfigure(i, weight=0)

        if self.file_tree_expanded:
            self.grid_rowconfigure(2, weight=1)
            self.file_tree_frame.grid(
                row=2, column=0, columnspan=2, rowspan=4, padx=10, pady=5, sticky="nsew"
            )
            self.expand_file_tree_button.configure(text="↙")
            self.expand_prompt_button.configure(text="↗")
            self.file_tree_frame.after(10, self.expand_file_tree_button.lift)

        elif self.prompt_expanded:
            self.grid_rowconfigure(2, weight=1)
            self.final_prompt_frame.grid(
                row=2, column=0, columnspan=2, rowspan=4, padx=10, pady=5, sticky="nsew"
            )
            self.expand_prompt_button.configure(text="↙")
            self.expand_file_tree_button.configure(text="↗")
            self.final_prompt_frame.after(10, self.expand_prompt_button.lift)

        else:
            for i, weight in self.default_row_weights.items():
                if i >= 2:
                    self.grid_rowconfigure(i, weight=weight)

            self.file_tree_frame.grid(**self.file_tree_frame_orig_grid)
            self.custom_ignore_frame.grid(**self.custom_ignore_frame_orig_grid)
            self.right_pane.grid(**self.right_pane_orig_grid)
            self.final_prompt_frame.grid(**self.final_prompt_frame_orig_grid)
            self.expand_file_tree_button.configure(text="↗")
            self.expand_prompt_button.configure(text="↗")
            self.file_tree_frame.after(10, self.expand_file_tree_button.lift)
            self.final_prompt_frame.after(10, self.expand_prompt_button.lift)

    def _set_textbox_content(self, textbox, content):
        if not textbox.winfo_exists():
            return
        current_pos = textbox.yview()
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.insert("1.0", content)
        textbox.configure(state="disabled")
        textbox.yview_moveto(current_pos[0])

    def _update_ui_busy_state(self):
        is_busy = self.active_background_tasks > 0
        new_state = "disabled" if is_busy else "normal"

        if is_busy:
            if not self.progress_popup or not self.progress_popup.winfo_exists():
                logging.debug("Creating Progress Popup")
                try:
                    self.progress_popup = CTkProgressPopup(
                        master=self,
                        title="Processing...",
                        label="Working...",
                        message=f"{self.active_background_tasks} task(s).",
                        side="right_bottom",
                    )
                except Exception as e:
                    logging.error(f"Failed to create CTkProgressPopup: {e}")
                    self.progress_popup = None
            elif self.progress_popup:
                try:
                    self.progress_popup.update_message(
                        f"{self.active_background_tasks} task(s)."
                    )
                except Exception:
                    pass
            logging.debug(f"UI BUSY ({self.active_background_tasks} tasks)")
        else:
            if self.progress_popup and self.progress_popup.winfo_exists():
                logging.debug("Cancelling Progress Popup")
                try:
                    self.progress_popup.cancel_task()
                except Exception:
                    pass
                self.progress_popup = None
            logging.debug("UI IDLE")

        for control in self._controls_to_disable_while_loading:
            if control and control.winfo_exists():
                is_main_content_frame = control in [
                    self.file_tree_frame,
                    self.custom_ignore_frame,
                    self.right_pane,
                    self.final_prompt_frame,
                ]

                if isinstance(control, CTkListbox):
                    pass
                elif (
                    control == self.expand_file_tree_button
                    and not self.file_tree_frame.winfo_ismapped()
                ):
                    pass
                elif (
                    control == self.expand_prompt_button
                    and not self.final_prompt_frame.winfo_ismapped()
                ):
                    pass
                else:
                    try:
                        control.configure(state=new_state)
                    except Exception as e:
                        logging.warning(f"Could not set state for {control}: {e}")

        if not is_busy and self.use_gitignore_checkbox.winfo_exists():
            try:
                from gitignore_parser import (
                    parse_gitignore,
                )
            except ImportError:
                self.use_gitignore_checkbox.configure(state="disabled")

    def _submit_task(self, task_fn, on_done_fn, *args, **kwargs):
        self.active_background_tasks += 1
        self._update_ui_busy_state()
        logging.debug(f"Submitting task: {task_fn.__name__}")
        try:
            future = self.executor.submit(task_fn, *args, **kwargs)
            future.add_done_callback(
                lambda f: self._generic_task_done_handler(f, on_done_fn)
            )
        except Exception as e:
            logging.error(f"Failed to submit task {task_fn.__name__}: {e}")
            self.active_background_tasks = max(0, self.active_background_tasks - 1)
            self._update_ui_busy_state()

    def _generic_task_done_handler(self, future, on_done_fn):
        try:
            result = future.result()
            self.ui_queue.put((on_done_fn, result, None))
        except Exception as e:
            logging.error(f"Exception in background task: {e}", exc_info=True)
            self.ui_queue.put((on_done_fn, None, e))
        finally:
            self.ui_queue.put(("decrement_task_counter", None, None))

    def _process_ui_queue(self):
        try:
            while True:
                callback_fn_or_cmd_key, data, error = self.ui_queue.get_nowait()
                if callback_fn_or_cmd_key == "decrement_task_counter":
                    self.active_background_tasks = max(
                        0, self.active_background_tasks - 1
                    )
                    self._update_ui_busy_state()
                elif callback_fn_or_cmd_key == "task_error_message":
                    title, msg = data
                    CTkMessagebox(master=self, title=title, message=msg, icon="cancel")
                elif callable(callback_fn_or_cmd_key):
                    if error:
                        logging.error(
                            f"Error for UI callback {callback_fn_or_cmd_key.__name__}: {error}"
                        )
                        CTkMessagebox(
                            master=self,
                            title="Background Task Error",
                            message=f"An error occurred: {error}",
                            icon="cancel",
                        )
                    else:
                        callback_fn_or_cmd_key(data)
                else:
                    logging.warning(
                        f"Unknown item in UI queue: {callback_fn_or_cmd_key}"
                    )
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_ui_queue)

    def _on_custom_ignore_typed(self, event=None):
        if self.custom_ignore_debounce_timer:
            self.after_cancel(self.custom_ignore_debounce_timer)
        self.custom_ignore_debounce_timer = self.after(
            750, self._debounced_refresh_all_views_and_prompt
        )

    def _on_instructions_typed(self, event=None):
        if self.instructions_debounce_timer:
            self.after_cancel(self.instructions_debounce_timer)
        self.instructions_debounce_timer = self.after(
            750, self.trigger_generate_prompt_stand_alone
        )

    def _debounced_refresh_all_views_and_prompt(self):
        logging.debug("Debounced action: Refreshing all views and prompt.")
        self._orchestrate_full_refresh()

    def _build_file_tree_task(
        self,
        folder_path: Path,
        gitignore_matcher,
        use_gitignore,
        custom_patterns,
        project_root_path,
    ):
        logging.debug(f"Task: Building file tree for {folder_path}")
        return build_file_tree_string(
            folder_path,
            gitignore_matcher=gitignore_matcher,
            use_gitignore_flag=use_gitignore,
            custom_ignore_patterns=custom_patterns,
            project_root_path_for_custom=project_root_path,
        )

    def _generate_prompt_task(
        self,
        instructions,
        file_tree_text,
        main_file_paths_list,
        project_folder_name,
        project_root_path_obj,
        use_gitignore_val,
        custom_patterns_list,
    ):
        logging.debug("Task: Generating prompt content.")
        prompt_parts = []
        if instructions:
            prompt_parts.extend(["--- INSTRUCTIONS ---", instructions, "\n"])

        if project_folder_name:
            prompt_parts.append(f"--- PROJECT CONTEXT: {project_folder_name} ---")
            filter_status = []
            if (
                use_gitignore_val
                and self.use_gitignore_checkbox.winfo_exists()
                and not self.use_gitignore_checkbox.cget("state") == "disabled"
            ):
                filter_status.append(".gitignore active")
            if custom_patterns_list:
                filter_status.append("custom ignores active")
            if FALLBACK_IGNORE_DIRS or FALLBACK_IGNORE_FILES:
                filter_status.append("default ignores active")
            status_str = f" (Filters: {', '.join(filter_status) if filter_status else 'none active'})"

            if (
                file_tree_text
                and file_tree_text != "(No files to display or all ignored)"
            ):
                prompt_parts.extend(
                    [f"File Tree Structure{status_str}:", file_tree_text]
                )
            else:
                prompt_parts.append(
                    f"File Tree Structure: (No files to display or all files were ignored by filters{status_str})"
                )
            prompt_parts.append("\n")

        if main_file_paths_list:
            prompt_parts.append("--- MAIN FILE(S) CONTENT ---")
            for file_path_str in main_file_paths_list:
                file_p = Path(file_path_str)
                display_path_in_prompt = file_p.name
                if project_root_path_obj:
                    try:
                        abs_file_p = file_p.resolve(strict=False)
                        abs_project_root = project_root_path_obj.resolve(strict=False)
                        if abs_file_p.is_relative_to(abs_project_root):
                            display_path_in_prompt = str(
                                abs_file_p.relative_to(abs_project_root)
                            )
                        else:
                            display_path_in_prompt = str(abs_file_p)
                    except (ValueError, OSError):
                        display_path_in_prompt = (
                            str(file_p) if str(file_p) != "." else file_p.name
                        )

                prompt_parts.append(
                    f"--- File: {display_path_in_prompt.replace(os.sep, '/')} ---"
                )
                prompt_parts.append(read_file_content(file_path_str).strip())
                prompt_parts.append("--- End File ---")
            prompt_parts.append("\n")
        else:
            prompt_parts.extend(
                ["--- MAIN FILE(S) CONTENT ---", "(No main files added to the list.)\n"]
            )
        return "\n".join(prompt_parts).strip()

    def _update_file_tree_ui(self, tree_string):
        logging.debug("UI Update: Setting file tree content.")
        self._set_textbox_content(
            self.file_tree_textbox,
            tree_string if tree_string else "(No files to display or all ignored)",
        )
        if hasattr(self, "_chain_step") and self._chain_step == "file_tree_done":
            self._orchestrate_full_refresh_step_prompt_gen()

    def _update_final_prompt_ui(self, prompt_string):
        logging.debug("UI Update: Setting final prompt content.")
        self._set_textbox_content(self.final_prompt_textbox, prompt_string)
        if hasattr(self, "_chain_step") and self._chain_step == "prompt_done":
            logging.debug("Chain step 'prompt_done' complete.")
            del self._chain_step

    def _on_project_label_configure(self, event):
        label = self.project_location_label
        if not label.winfo_exists():
            return

        width = label.winfo_width()
        buffer = 10
        new_wraplength = width - buffer

        if new_wraplength > 0:
            if label.cget("wraplength") != new_wraplength:
                label.configure(wraplength=new_wraplength)
        elif label.cget("wraplength") != 0:
            label.configure(wraplength=1)

    def _update_project_location_label(self):
        if self.project_folder_path and self.project_location_label.winfo_exists():
            self.project_location_label.configure(
                text=f"Project location: {self.project_folder_path}"
            )
        elif self.project_location_label.winfo_exists():
            self.project_location_label.configure(text="Project: Not selected")

        if self.project_location_label.winfo_exists():
            self.project_location_label.after_idle(
                lambda: self._on_project_label_configure(None)
            )

    def _orchestrate_full_refresh(self):
        if self.active_background_tasks > 0:
            logging.warning("Orchestrator: App busy, full refresh deferred.")
            return

        self._update_project_location_label()

        if not self.project_folder_path:
            self._set_textbox_content(self.file_tree_textbox, "")
            self.trigger_generate_prompt_stand_alone()
            return

        logging.debug("Orchestrator: Starting full refresh Step 1 (File Tree)")
        if self.progress_popup and self.progress_popup.winfo_exists():
            self.progress_popup.update_label("Building file tree...")
            self.progress_popup.update_progress(0.1)
        self._chain_step = "file_tree_done"
        self._load_gitignore()
        custom_patterns = self._get_custom_ignore_patterns()
        self._submit_task(
            self._build_file_tree_task,
            self._update_file_tree_ui,
            self.project_folder_path,
            self.gitignore_matcher,
            self.use_gitignore_var.get(),
            custom_patterns,
            self.project_folder_path,
        )

    def _orchestrate_full_refresh_step_prompt_gen(self):
        logging.debug("Orchestrator: Step - Prompt Gen (after file tree)")
        if self.progress_popup and self.progress_popup.winfo_exists():
            self.progress_popup.update_label("Generating final prompt...")
            self.progress_popup.update_progress(0.8)
        self._chain_step = "prompt_done"
        self.trigger_generate_prompt_stand_alone(is_part_of_chain=True)

    def _validate_main_file_paths(self):
        original_count = len(self.main_file_paths)
        existing_files = [p for p in self.main_file_paths if Path(p).is_file()]
        if len(existing_files) < original_count:
            logging.info(
                f"Removed {original_count - len(existing_files)} non-existent "
                "files from the main files list."
            )
            self.main_file_paths = existing_files
            return True
        return False

    def trigger_generate_prompt_stand_alone(self, event=None, is_part_of_chain=False):
        if not is_part_of_chain and self.active_background_tasks > 0:
            logging.warning("Trigger Prompt: App busy, request deferred.")
            return

        if self._validate_main_file_paths():
            self._rebuild_listbox_from_main_file_paths()

        logging.debug(
            f"Triggering prompt generation (is_part_of_chain={is_part_of_chain})."
        )
        instructions = self.instructions_textbox.get("1.0", "end-1c").strip()
        file_tree = self.file_tree_textbox.get("1.0", "end-1c").strip()
        project_name = (
            self.project_folder_path.name if self.project_folder_path else None
        )
        self._submit_task(
            self._generate_prompt_task,
            self._update_final_prompt_ui,
            instructions,
            file_tree,
            list(self.main_file_paths),
            project_name,
            self.project_folder_path,
            self.use_gitignore_var.get(),
            self._get_custom_ignore_patterns(),
        )

    def _get_custom_ignore_patterns(self):
        patterns_str = self.custom_ignore_textbox.get("1.0", "end-1c")
        return [p.strip() for p in patterns_str.splitlines() if p.strip()]

    def _load_gitignore(self):
        self.gitignore_matcher = lambda path_to_check: False
        if self.use_gitignore_checkbox.winfo_exists():
            self.use_gitignore_checkbox.configure(state="normal")
        try:
            from gitignore_parser import (
                parse_gitignore,
            )

            if self.project_folder_path and self.use_gitignore_var.get():
                gitignore_file_path = self.project_folder_path / ".gitignore"
                if gitignore_file_path.is_file():
                    try:
                        self.gitignore_matcher = parse_gitignore(
                            str(gitignore_file_path),
                            base_dir=str(self.project_folder_path),
                        )
                        logging.info(f".gitignore loaded from {gitignore_file_path}")
                    except Exception as e:
                        logging.error(f"Error parsing .gitignore: {e}")
                else:
                    logging.info(
                        f".gitignore not found or not a file in {self.project_folder_path}"
                    )
        except ImportError:
            logging.warning("gitignore_parser not found. .gitignore disabled.")
            if self.use_gitignore_checkbox.winfo_exists():
                self.use_gitignore_checkbox.configure(state="disabled")
            self.use_gitignore_var.set(False)
        except Exception as e:
            logging.error(f"Error in _load_gitignore: {e}")

    def open_project_folder(self):
        logging.info("Attempting to open project folder...")
        folder_path_str = filedialog.askdirectory(title="Select Project Folder")
        if folder_path_str:
            new_project_path = Path(folder_path_str)
            if self.project_folder_path != new_project_path:
                self.project_folder_path = new_project_path
                self.title(f"LLM Prompt Generator - {self.project_folder_path.name}")
                logging.info(f"Project folder selected: {self.project_folder_path}")

                self.main_file_paths = []
                self._rebuild_listbox_from_main_file_paths()
                self._orchestrate_full_refresh()
            else:
                logging.info("Same project folder selected again. No change.")
        else:
            logging.info("Project folder selection cancelled.")
            if not self.project_folder_path:
                self._update_project_location_label()
                self._orchestrate_full_refresh()

    def _get_display_path(self, full_path_str: str) -> str:
        full_path_obj = Path(full_path_str)
        if self.project_folder_path:
            try:
                abs_full_path = full_path_obj.resolve(strict=True)
                abs_project_path = self.project_folder_path.resolve(strict=True)

                if abs_full_path.is_relative_to(abs_project_path):
                    return str(abs_full_path.relative_to(abs_project_path))
            except (FileNotFoundError, RuntimeError, ValueError):
                pass
        return full_path_str

    def _rebuild_listbox_from_main_file_paths(self):
        if not self.main_files_listbox.winfo_exists():
            return
        self.main_files_listbox.delete("all")
        for full_path_str in self.main_file_paths:
            display_path = self._get_display_path(full_path_str)
            self.main_files_listbox.insert("END", display_path)
        logging.debug(
            f"Rebuilt main_files_listbox with {len(self.main_file_paths)} items."
        )

    def add_files_from_folder(self):
        logging.debug("Adding files from folder (non-recursive)...")
        if not self.project_folder_path:
            CTkMessagebox(
                master=self,
                title="No Project",
                message="Please open a project folder first.",
                icon="warning",
            )
            return

        start_dir = str(self.project_folder_path)
        folder_path_str = filedialog.askdirectory(
            title="Select Folder (non-recursive file addition)", initialdir=start_dir
        )

        if folder_path_str:
            selected_folder_path_obj = Path(folder_path_str)
            logging.info(
                f"Folder selected for file addition: {selected_folder_path_obj}"
            )

            custom_patterns = self._get_custom_ignore_patterns()
            use_gitignore = self.use_gitignore_var.get()

            added_count = 0
            try:
                for item_path_obj in selected_folder_path_obj.iterdir():
                    try:
                        if not item_path_obj.is_file():
                            continue
                    except OSError:
                        logging.warning(
                            f"OSError checking if {item_path_obj} is a file. Skipping."
                        )
                        continue

                    item_name = item_path_obj.name
                    is_ignored_by_project_gitignore = False
                    if (
                        use_gitignore
                        and self.gitignore_matcher
                        and self.project_folder_path
                    ):
                        try:
                            resolved_item_path = item_path_obj.resolve(strict=False)
                            resolved_project_root = self.project_folder_path.resolve(
                                strict=False
                            )

                            if resolved_item_path.is_relative_to(resolved_project_root):
                                if self.gitignore_matcher(item_path_obj):
                                    is_ignored_by_project_gitignore = True
                        except (ValueError, OSError) as e:
                            logging.warning(
                                f"Could not determine if {item_path_obj} is relative to project {self.project_folder_path} "
                                f"for .gitignore check: {e}. Assuming not ignored by project .gitignore."
                            )
                            pass

                    if is_ignored_by_project_gitignore:
                        continue
                    if _is_custom_ignored(
                        item_path_obj, self.project_folder_path, custom_patterns
                    ):
                        continue
                    if (
                        item_name in FALLBACK_IGNORE_FILES
                        or any(
                            fnmatch.fnmatch(item_name, pat)
                            for pat in FALLBACK_IGNORE_FILES
                            if "*" in pat
                        )
                        or (
                            item_name.startswith(".")
                            and item_name
                            not in {".gitignore", ".gitattributes", ".gitmodules"}
                        )
                    ):
                        continue

                    full_path_str = str(item_path_obj.resolve(strict=False))
                    if full_path_str not in self.main_file_paths:
                        self.main_file_paths.append(full_path_str)
                        added_count += 1

                if added_count > 0:
                    self._rebuild_listbox_from_main_file_paths()
                    self.trigger_generate_prompt_stand_alone()
                logging.info(
                    f"Added {added_count} files from {selected_folder_path_obj.name}"
                )
            except PermissionError as e:
                logging.error(
                    f"Permission error when trying to iterate/access files in {selected_folder_path_obj}: {e}",
                )
                CTkMessagebox(
                    master=self,
                    title="Permission Error",
                    message=f"Cannot access files in the selected folder due to permission issues:\n{selected_folder_path_obj}",
                    icon="cancel",
                )
            except Exception as e:
                logging.error(
                    f"Error adding files from folder {selected_folder_path_obj}: {e}",
                    exc_info=True,
                )
                CTkMessagebox(
                    master=self,
                    title="Error",
                    message=f"Could not read folder contents: {e}",
                    icon="cancel",
                )

    def add_files_from_folder_recursively(self):
        logging.debug("Adding files from folder (recursive)...")
        if not self.project_folder_path:
            CTkMessagebox(
                master=self,
                title="No Project",
                message="Please open a project folder first.",
                icon="warning",
            )
            return

        start_dir = str(self.project_folder_path)
        folder_path_str = filedialog.askdirectory(
            title="Select Folder to Add Recursively", initialdir=start_dir
        )

        if not folder_path_str:
            return

        selected_folder_path_obj = Path(folder_path_str)
        logging.info(
            f"Folder selected for recursive file addition: {selected_folder_path_obj}"
        )

        custom_patterns = self._get_custom_ignore_patterns()
        use_gitignore = self.use_gitignore_var.get()
        added_count = 0

        try:
            for root, dirs, files in os.walk(str(selected_folder_path_obj)):
                root_path = Path(root)

                dirs[:] = [
                    d
                    for d in dirs
                    if not self._is_dir_ignored(
                        root_path / d, use_gitignore, custom_patterns
                    )
                ]

                for filename in files:
                    file_path = root_path / filename
                    if not self._is_file_ignored(
                        file_path, use_gitignore, custom_patterns
                    ):
                        full_path_str = str(file_path.resolve(strict=False))
                        if full_path_str not in self.main_file_paths:
                            self.main_file_paths.append(full_path_str)
                            added_count += 1

            if added_count > 0:
                self._rebuild_listbox_from_main_file_paths()
                self.trigger_generate_prompt_stand_alone()
            logging.info(
                f"Added {added_count} files recursively from {selected_folder_path_obj.name}"
            )
            CTkMessagebox(
                master=self,
                title="Success",
                message=f"Added {added_count} files.",
                icon="check",
            )

        except Exception as e:
            logging.error(
                f"Error during recursive file addition from {selected_folder_path_obj}: {e}",
                exc_info=True,
            )
            CTkMessagebox(
                master=self,
                title="Error",
                message=f"An unexpected error occurred: {e}",
                icon="cancel",
            )

    def _is_dir_ignored(self, dir_path, use_gitignore, custom_patterns):
        dir_name = dir_path.name
        if (
            use_gitignore
            and self.gitignore_matcher
            and self.gitignore_matcher(dir_path)
        ):
            return True
        if _is_custom_ignored(dir_path, self.project_folder_path, custom_patterns):
            return True
        if (
            dir_name in FALLBACK_IGNORE_DIRS
            or any(
                fnmatch.fnmatch(dir_name, pat)
                for pat in FALLBACK_IGNORE_DIRS
                if "*" in pat
            )
            or (dir_name.startswith(".") and dir_name not in {".well-known"})
        ):
            return True
        return False

    def _is_file_ignored(self, file_path, use_gitignore, custom_patterns):
        file_name = file_path.name
        if (
            use_gitignore
            and self.gitignore_matcher
            and self.gitignore_matcher(file_path)
        ):
            return True
        if _is_custom_ignored(file_path, self.project_folder_path, custom_patterns):
            return True
        if (
            file_name in FALLBACK_IGNORE_FILES
            or any(
                fnmatch.fnmatch(file_name, pat)
                for pat in FALLBACK_IGNORE_FILES
                if "*" in pat
            )
            or (
                file_name.startswith(".")
                and file_name not in {".gitignore", ".gitattributes", ".gitmodules"}
            )
        ):
            return True
        return False

    def add_individual_files(self):
        logging.debug("Adding individual main files...")
        start_dir = (
            str(self.project_folder_path) if self.project_folder_path else os.getcwd()
        )
        file_paths_tuple = filedialog.askopenfilenames(
            title="Select Individual Main Files", initialdir=start_dir
        )

        if file_paths_tuple:
            added_count = 0
            for p_str in file_paths_tuple:
                full_path_str = str(Path(p_str).resolve(strict=False))
                if full_path_str not in self.main_file_paths:
                    self.main_file_paths.append(full_path_str)
                    added_count += 1

            if added_count > 0:
                self._rebuild_listbox_from_main_file_paths()
                self.trigger_generate_prompt_stand_alone()
            logging.info(f"Added {added_count} individual files.")

    def unselect_main_files(self):
        if not self.main_files_listbox.winfo_exists():
            return
        selected_indices = self.main_files_listbox.curselection()
        if not selected_indices:
            CTkMessagebox(
                master=self,
                title="Info",
                message="No files selected in the list to remove.",
                icon="info",
            )
            return

        for index in sorted(selected_indices, reverse=True):
            if 0 <= index < len(self.main_file_paths):
                del self.main_file_paths[index]
            else:
                logging.warning(
                    f"Attempted to delete out-of-bounds index {index} from main_file_paths"
                )

        self._rebuild_listbox_from_main_file_paths()
        self.trigger_generate_prompt_stand_alone()

    def copy_prompt(self):
        prompt_text = self.final_prompt_textbox.get("1.0", "end-1c")
        if not prompt_text:
            CTkMessagebox(
                master=self,
                title="Copy Prompt",
                message="Nothing to copy.",
                icon="info",
            )
            return
        try:
            pyperclip.copy(prompt_text)
            CTkMessagebox(
                master=self,
                title="Copy Prompt",
                message="Prompt copied to clipboard!",
                icon="check",
            )
        except Exception as e:
            logging.error(f"Copy prompt error: {e}")
            CTkMessagebox(
                master=self,
                title="Copy Error",
                message=f"Could not copy to clipboard: {e}",
                icon="cancel",
            )

    def _close_config_manager(self):
        if self.config_toplevel and self.config_toplevel.winfo_exists():
            try:
                self.config_toplevel.grab_release()
                self.config_toplevel.destroy()
            except Exception as e:
                logging.warning(f"Error closing config manager: {e}")
        self.config_toplevel = None

    def _open_config_manager(self):
        if self.config_toplevel and self.config_toplevel.winfo_exists():
            self.config_toplevel.focus()
            return

        self.config_toplevel = ctk.CTkToplevel(self)
        self.config_toplevel.title("Configuration Manager")
        ws = self.winfo_screenwidth()
        hs = self.winfo_screenheight()
        w, h = 600, 450
        x = (ws / 2) - (w / 2)
        y = (hs / 2) - (h / 2)
        self.config_toplevel.geometry("%dx%d+%d+%d" % (w, h, x, y))

        self.config_toplevel.grab_set()
        if self.icon_path:
            self.config_toplevel.after(
                200, lambda: self.config_toplevel.iconbitmap(self.icon_path)
            )
        self.config_toplevel.protocol("WM_DELETE_WINDOW", self._close_config_manager)

        left_frame = ctk.CTkFrame(self.config_toplevel)
        left_frame.pack(side="left", fill="y", padx=10, pady=10)
        ctk.CTkLabel(left_frame, text="Saved Configurations:").pack(pady=(0, 5))
        self.config_listbox = CTkListbox(left_frame, command=self._on_config_select)
        self.config_listbox.pack(expand=True, fill="both")

        right_frame = ctk.CTkFrame(self.config_toplevel)
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(right_frame, text="Configuration Name:").pack(anchor="w", padx=10)
        self.config_name_entry = ctk.CTkEntry(right_frame)
        self.config_name_entry.pack(fill="x", pady=(0, 10), padx=10)
        button_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        button_frame.pack(fill="x", pady=5, padx=5)
        ctk.CTkButton(
            button_frame, text="Load", command=self._load_selected_config
        ).pack(side="left", padx=5, expand=True, fill="x")
        ctk.CTkButton(
            button_frame, text="Save", command=self._save_current_config
        ).pack(side="left", padx=5, expand=True, fill="x")
        ctk.CTkButton(
            right_frame,
            text="Delete Selected",
            command=self._delete_selected_config,
            fg_color="red",
            hover_color="darkred",
        ).pack(fill="x", pady=5, padx=10)
        ctk.CTkButton(
            right_frame, text="Refresh List", command=self._populate_config_listbox
        ).pack(fill="x", pady=(10, 0), padx=10)
        self._populate_config_listbox()

    def _on_config_select(self, selected_value):
        if selected_value:
            self.config_name_entry.delete(0, "end")
            self.config_name_entry.insert(0, selected_value)

    def _populate_config_listbox(self):
        if (
            not hasattr(self, "config_listbox")
            or not self.config_listbox.winfo_exists()
        ):
            return
        current_selection_value = self.config_listbox.get()
        self.config_listbox.delete("all")
        if not self.config_dir.exists():
            return
        config_files = sorted(self.config_dir.glob("*.ini"))
        for i, f_path in enumerate(config_files):
            self.config_listbox.insert(i, f_path.stem)
        if current_selection_value:
            try:
                all_items = [
                    self.config_listbox.get(i)
                    for i in range(self.config_listbox.size())
                ]
                if current_selection_value in all_items:
                    idx = all_items.index(current_selection_value)
                    self.config_listbox.select(idx)
                    if hasattr(self.config_listbox, "activate"):
                        self.config_listbox.activate(idx)
            except Exception as e:
                logging.warning(f"Could not reselect config item: {e}")

    def _save_current_config(self):
        name = self.config_name_entry.get().strip()
        if not name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="Config name cannot be empty.",
                icon="cancel",
            )
            return
        if not all(c.isalnum() or c in ["_", "-"] for c in name):
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="Name: letters, numbers, _, - only.",
                icon="cancel",
            )
            return

        config = configparser.ConfigParser()
        config["Settings"] = {
            "Instructions": self.instructions_textbox.get("1.0", "end-1c"),
            "CustomIgnores": self.custom_ignore_textbox.get("1.0", "end-1c"),
            "ProjectFolder": (
                str(self.project_folder_path) if self.project_folder_path else ""
            ),
            "MainFiles": "\n".join(self.main_file_paths),
        }
        file_path = self.config_dir / f"{name}.ini"
        try:
            with open(file_path, "w", encoding="utf-8") as configfile:
                config.write(configfile)
            logging.info(f"Saved configuration: {name}")
            CTkMessagebox(
                master=self.config_toplevel,
                title="Success",
                message=f"Config '{name}' saved.",
                icon="check",
            )
            self._populate_config_listbox()
        except Exception as e:
            logging.error(f"Error saving config '{name}': {e}")
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message=f"Failed to save config: {e}",
                icon="cancel",
            )

    def _load_selected_config(self):
        selected_name = (
            self.config_listbox.get()
            if hasattr(self, "config_listbox") and self.config_listbox.winfo_exists()
            else None
        )
        if not selected_name:
            selected_name = self.config_name_entry.get().strip()
        if not selected_name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="No config selected/named.",
                icon="warning",
            )
            return

        file_path = self.config_dir / f"{selected_name}.ini"
        if not file_path.is_file():
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message=f"Config file for '{selected_name}' not found.",
                icon="cancel",
            )
            return

        config = configparser.ConfigParser()
        try:
            config.read(file_path, encoding="utf-8")
            instructions = config.get("Settings", "Instructions", fallback="")
            custom_ignores = config.get("Settings", "CustomIgnores", fallback="")
            project_folder_str = config.get("Settings", "ProjectFolder", fallback="")
            main_files_str = config.get("Settings", "MainFiles", fallback="")

            self._set_textbox_content(self.instructions_textbox, instructions)
            self._set_textbox_content(self.custom_ignore_textbox, custom_ignores)

            self._close_config_manager()

            project_changed_or_set = False
            new_project_path = None
            if project_folder_str:
                loaded_project_path = Path(project_folder_str)
                if loaded_project_path.is_dir():
                    new_project_path = loaded_project_path
                    logging.info(
                        f"Project folder loaded from config: {new_project_path}"
                    )
                else:
                    logging.warning(
                        f"Project folder from config not found: {project_folder_str}"
                    )
                    CTkMessagebox(
                        master=self,
                        title="Warning",
                        message=f"Project folder from config not found:\n{project_folder_str}",
                        icon="warning",
                    )

            if self.project_folder_path != new_project_path:
                self.project_folder_path = new_project_path
                project_changed_or_set = True
                if self.project_folder_path:
                    self.title(
                        f"LLM Prompt Generator - {self.project_folder_path.name}"
                    )
                else:
                    self.title("LLM Prompt Generator")

            self._update_project_location_label()

            self.main_file_paths = []
            loaded_any_main_files = False
            if main_files_str:
                potential_paths = [
                    p.strip() for p in main_files_str.splitlines() if p.strip()
                ]
                for p_str in potential_paths:
                    file_path_obj = Path(p_str)
                    if file_path_obj.is_file():
                        resolved_path = str(file_path_obj.resolve(strict=False))
                        self.main_file_paths.append(resolved_path)
                        loaded_any_main_files = True
                    else:
                        logging.warning(f"Main file from config not found: {p_str}")
                        CTkMessagebox(
                            master=self,
                            title="Warning",
                            message=f"Main file from config not found (skipped):\n{p_str}",
                            icon="warning",
                        )

            self._rebuild_listbox_from_main_file_paths()

            if project_changed_or_set:
                self._orchestrate_full_refresh()
            elif loaded_any_main_files or not main_files_str:
                self.trigger_generate_prompt_stand_alone()

            logging.info(f"Loaded configuration: {selected_name}")
            CTkMessagebox(
                master=self,
                title="Success",
                message=f"Configuration '{selected_name}' loaded.",
                icon="check",
            )

        except Exception as e:
            logging.error(
                f"Error loading configuration '{selected_name}': {e}", exc_info=True
            )
            msg_master = (
                self.config_toplevel
                if self.config_toplevel and self.config_toplevel.winfo_exists()
                else self
            )
            CTkMessagebox(
                master=msg_master,
                title="Error",
                message=f"Failed to load config: {e}",
                icon="cancel",
            )

    def _delete_selected_config(self):
        selected_name = (
            self.config_listbox.get()
            if hasattr(self, "config_listbox") and self.config_listbox.winfo_exists()
            else None
        )
        if not selected_name:
            selected_name = self.config_name_entry.get().strip()
        if not selected_name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="No config selected/named.",
                icon="warning",
            )
            return

        msg = CTkMessagebox(
            master=self.config_toplevel,
            title="Confirm Delete",
            message=f"Delete '{selected_name}'?",
            icon="question",
            option_1="No",
            option_2="Yes",
        )
        if msg.get() == "Yes":
            file_path = self.config_dir / f"{selected_name}.ini"
            try:
                file_path.unlink(missing_ok=True)
                logging.info(f"Deleted configuration: {selected_name}")
                CTkMessagebox(
                    master=self.config_toplevel,
                    title="Success",
                    message=f"Config '{selected_name}' deleted.",
                    icon="check",
                )
                if self.config_name_entry.get().strip() == selected_name:
                    self.config_name_entry.delete(0, "end")
                self._populate_config_listbox()
            except Exception as e:
                logging.error(f"Error deleting config '{selected_name}': {e}")
                CTkMessagebox(
                    master=self.config_toplevel,
                    title="Error",
                    message=f"Failed to delete config: {e}",
                    icon="cancel",
                )


if __name__ == "__main__":
    app = LLMPromptApp()
    app.mainloop()
