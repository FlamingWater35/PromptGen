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
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

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
}
FALLBACK_IGNORE_FILES = {".DS_Store", "*.pyc", "*.log", "*.swp", "*.swo"}
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024


def _is_custom_ignored(item_path, project_root_path, custom_patterns):
    if not custom_patterns or not project_root_path:
        return False
    item_name = item_path.name
    is_dir = item_path.is_dir()
    try:
        abs_item_path = item_path.resolve()
        abs_project_root_path = project_root_path.resolve()
        if not abs_item_path.is_relative_to(abs_project_root_path):
            relative_item_path_str = None
        else:
            relative_item_path_str = str(
                abs_item_path.relative_to(abs_project_root_path)
            )
    except ValueError:
        relative_item_path_str = None
    except Exception:
        relative_item_path_str = None

    for pattern_str in custom_patterns:
        pattern = pattern_str.strip()
        if not pattern:
            continue
        if fnmatch.fnmatch(item_name.lower(), pattern.lower()):
            return True
        if relative_item_path_str:
            norm_rel_path = relative_item_path_str.replace(os.sep, "/")
            norm_pattern = pattern.replace(os.sep, "/")
            if norm_pattern.endswith("/"):
                if is_dir and fnmatch.fnmatch(norm_rel_path + "/", norm_pattern):
                    return True
            elif fnmatch.fnmatch(norm_rel_path, norm_pattern):
                return True
    return False


def build_file_tree_string(
    folder_path_str,
    indent="",
    tree_lines=None,
    gitignore_matcher=None,
    use_gitignore_flag=True,
    custom_ignore_patterns=None,
    project_root_path_for_custom=None,
):
    folder_path = Path(folder_path_str)
    if tree_lines is None:
        tree_lines = []
    visible_items_data = []
    try:
        original_items = os.listdir(folder_path)
    except PermissionError:
        tree_lines.append(f"{indent}[ACCESS DENIED] {folder_path.name}")
        return "\n".join(tree_lines)
    except FileNotFoundError:
        tree_lines.append(f"{indent}[NOT FOUND] {folder_path.name}")
        return "\n".join(tree_lines)
    for item_name in original_items:
        item_path_obj = folder_path / item_name
        is_dir = item_path_obj.is_dir()
        if is_dir and item_name.startswith("."):
            if item_name not in FALLBACK_IGNORE_DIRS:
                pass
            if item_name in FALLBACK_IGNORE_DIRS:
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
                or item_name.startswith(".")
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
                    and item_name not in [".gitignore", ".gitattributes", ".gitmodules"]
                )
            ):
                continue
        visible_items_data.append((item_name, item_path_obj, is_dir))
    visible_items_data.sort(key=lambda x: (not x[2], x[0].lower()))
    for i, (item_name, item_path_obj, is_dir) in enumerate(visible_items_data):
        is_last = i == len(visible_items_data) - 1
        connector = "└── " if is_last else "├── "
        if is_dir:
            tree_lines.append(f"{indent}{connector}{item_name}/")
            new_indent = indent + ("    " if is_last else "│   ")
            build_file_tree_string(
                str(item_path_obj),
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


def read_file_content(file_path_str):
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

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.project_folder_path = None
        self.main_file_paths = []
        self.main_files_source_mode = "Folder"
        self.main_files_selected_folder_path = None
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

        self.config_dir = Path.home() / "Documents" / "LLMPromptInitializerConfigs"
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Configuration directory: {self.config_dir}")
        except Exception as e:
            logging.error(f"Could not create config directory {self.config_dir}: {e}")
        self.config_toplevel = None

        self._setup_ui()
        self.update_main_files_ui(self.main_files_source_segmented_button.get())
        self._load_gitignore()
        self._process_ui_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

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
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(2, weight=3)
        self.grid_rowconfigure(3, weight=1)
        self.grid_rowconfigure(4, weight=0)
        self.grid_rowconfigure(5, weight=2)

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
        self.file_tree_frame.grid(row=2, column=0, padx=(10, 5), pady=5, sticky="nsew")
        self.file_tree_frame.grid_rowconfigure(1, weight=1)
        self.file_tree_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.file_tree_frame,
            text="Project File Tree:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.file_tree_textbox = ctk.CTkTextbox(
            self.file_tree_frame, wrap="none", state="disabled"
        )
        self.file_tree_textbox.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

        self.custom_ignore_frame = ctk.CTkFrame(self)
        self.custom_ignore_frame.grid(
            row=3, column=0, padx=(10, 5), pady=5, sticky="nsew"
        )
        self.custom_ignore_frame.grid_rowconfigure(1, weight=1)
        self.custom_ignore_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.custom_ignore_frame,
            text="Custom Ignore Patterns (one per line, fnmatch):",
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
        self.right_pane.grid(
            row=2, column=1, rowspan=2, padx=(5, 10), pady=5, sticky="nsew"
        )
        self.right_pane.grid_columnconfigure(0, weight=1)
        self.right_pane.grid_rowconfigure(1, weight=1)
        self.right_pane.grid_rowconfigure(6, weight=1)
        ctk.CTkLabel(
            self.right_pane,
            text="Instructions for LLM:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=(5, 0), sticky="w")
        self.instructions_textbox = ctk.CTkTextbox(
            self.right_pane, wrap="word", height=150
        )
        self.instructions_textbox.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.instructions_textbox.insert(
            "0.0", "Please act as a senior software engineer..."
        )
        self.instructions_textbox.bind("<KeyRelease>", self._on_instructions_typed)
        ctk.CTkLabel(
            self.right_pane, text="Main Files Source:", font=ctk.CTkFont(weight="bold")
        ).grid(row=2, column=0, padx=5, pady=(10, 0), sticky="w")
        self.main_files_source_segmented_button = ctk.CTkSegmentedButton(
            self.right_pane,
            values=["Folder", "Individual"],
            command=self.update_main_files_ui,
        )
        self.main_files_source_segmented_button.set("Folder")
        self.main_files_source_segmented_button.grid(
            row=3, column=0, padx=5, pady=5, sticky="ew"
        )
        self.main_files_button_frame = ctk.CTkFrame(
            self.right_pane, fg_color="transparent"
        )
        self.main_files_button_frame.grid(row=4, column=0, padx=5, pady=0, sticky="ew")
        self.main_files_button_frame.grid_columnconfigure(0, weight=1)
        self.select_main_folder_button = ctk.CTkButton(
            self.main_files_button_frame,
            text="Select Main Files Folder",
            command=self.select_main_files_folder,
        )
        self.select_individual_files_button = ctk.CTkButton(
            self.main_files_button_frame,
            text="Pick Individual Main Files",
            command=self.select_individual_main_files,
        )
        self.select_main_folder_button.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            self.right_pane,
            text="Selected Main Files:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=5, column=0, padx=5, pady=(5, 0), sticky="w")
        self.selected_main_files_textbox = ctk.CTkTextbox(
            self.right_pane, wrap="none", state="disabled", height=80
        )
        self.selected_main_files_textbox.grid(
            row=6, column=0, padx=5, pady=5, sticky="nsew"
        )

        self.final_prompt_frame = ctk.CTkFrame(self)
        self.final_prompt_frame.grid(
            row=5, column=0, columnspan=2, padx=10, pady=(5, 10), sticky="nsew"
        )
        self.final_prompt_frame.grid_rowconfigure(1, weight=1)
        self.final_prompt_frame.grid_columnconfigure(0, weight=1)
        self.final_prompt_frame.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(
            self.final_prompt_frame,
            text="Final Prompt:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=5, pady=(5, 0), sticky="w")
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
        self.manage_configs_button.pack(side="left", padx=(0, 10))

        self.copy_prompt_button = ctk.CTkButton(
            self.final_prompt_buttons_frame,
            text="Copy Prompt",
            command=self.copy_prompt,
        )
        self.copy_prompt_button.pack(side="left")

        self._controls_to_disable_while_loading = [
            self.open_project_button,
            self.use_gitignore_checkbox,
            self.custom_ignore_textbox,
            self.instructions_textbox,
            self.main_files_source_segmented_button,
            self.select_main_folder_button,
            self.select_individual_files_button,
            self.manage_configs_button,
        ]

    def _set_textbox_content(self, textbox, content):
        current_pos = textbox.yview() if textbox.winfo_exists() else (0.0,)
        if textbox.winfo_exists():
            textbox.configure(state="normal")
            textbox.delete("1.0", "end")
            textbox.insert("1.0", content)
            textbox.configure(state="disabled")
            textbox.yview_moveto(current_pos[0])

    def _update_ui_busy_state(self):
        if self.active_background_tasks > 0:
            if not self.progress_popup or not self.progress_popup.winfo_exists():
                logging.debug("Creating Progress Popup")
                try:
                    self.progress_popup = CTkProgressPopup(
                        master=self,
                        title="Processing...",
                        label="Working on background tasks.",
                        message=f"{self.active_background_tasks} task(s) running.",
                        side="right_bottom",
                    )
                except Exception as e:
                    logging.error(f"Failed to create CTkProgressPopup: {e}")
                    self.progress_popup = None
            elif self.progress_popup:
                try:
                    self.progress_popup.update_message(
                        f"{self.active_background_tasks} task(s) running."
                    )
                except Exception as e:
                    logging.warning(f"Could not update progress popup message: {e}")

            logging.debug(f"UI BUSY ({self.active_background_tasks} tasks)")
            for control in self._controls_to_disable_while_loading:
                if control and control.winfo_exists():
                    control.configure(state="disabled")
        else:
            if self.progress_popup and self.progress_popup.winfo_exists():
                logging.debug("Cancelling Progress Popup")
                try:
                    self.progress_popup.cancel_task()
                except Exception as e:
                    logging.warning(f"Error cancelling progress popup: {e}")
                self.progress_popup = None

            logging.debug("UI IDLE")
            for control in self._controls_to_disable_while_loading:
                if control and control.winfo_exists():
                    control.configure(state="normal")
            if (
                self.use_gitignore_checkbox
                and self.use_gitignore_checkbox.winfo_exists()
            ):
                try:
                    from gitignore_parser import parse_gitignore
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
            self.active_background_tasks -= 1
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
                    CTkMessagebox(title=title, message=msg, icon="cancel")
                elif callable(callback_fn_or_cmd_key):
                    if error:
                        logging.error(
                            f"Error for UI callback {callback_fn_or_cmd_key.__name__}: {error}"
                        )
                        CTkMessagebox(
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
        folder_path_str,
        gitignore_matcher,
        use_gitignore,
        custom_patterns,
        project_root_path,
    ):
        logging.debug(f"Task: Building file tree for {folder_path_str}")
        return build_file_tree_string(
            folder_path_str,
            gitignore_matcher=gitignore_matcher,
            use_gitignore_flag=use_gitignore,
            custom_ignore_patterns=custom_patterns,
            project_root_path_for_custom=project_root_path,
        )

    def _repopulate_main_files_task(
        self,
        folder_path_obj,
        gitignore_matcher,
        use_gitignore_flag,
        custom_patterns,
        project_root_path_for_custom_ignore,
    ):
        logging.debug(
            f"Task: Repopulating main files from {folder_path_obj} (recursive)"
        )
        main_files = []
        display_lines = [
            f"Selected Folder: {folder_path_obj.name}",
            "Files within (including subdirectories):",
        ]
        try:
            found_any = False
            for root, dirs, files in os.walk(str(folder_path_obj), topdown=True):
                dirs_to_process = list(dirs)
                dirs[:] = []

                for d_name in dirs_to_process:
                    dir_path = Path(root) / d_name

                    if d_name.startswith(".") and d_name not in FALLBACK_IGNORE_DIRS:
                        continue

                    if (
                        use_gitignore_flag
                        and gitignore_matcher
                        and gitignore_matcher(dir_path)
                    ):
                        continue

                    if _is_custom_ignored(
                        dir_path, project_root_path_for_custom_ignore, custom_patterns
                    ):
                        continue

                    if d_name in FALLBACK_IGNORE_DIRS or any(
                        fnmatch.fnmatch(d_name, pat)
                        for pat in FALLBACK_IGNORE_DIRS
                        if "*" in pat
                    ):
                        continue

                    dirs.append(d_name)

                for item_name in sorted(files, key=str.lower):
                    item_path = Path(root) / item_name

                    if (
                        use_gitignore_flag
                        and gitignore_matcher
                        and gitignore_matcher(item_path)
                    ):
                        continue

                    if project_root_path_for_custom_ignore and _is_custom_ignored(
                        item_path, project_root_path_for_custom_ignore, custom_patterns
                    ):
                        continue

                    is_fallback_ignored = False
                    if item_name in FALLBACK_IGNORE_FILES or any(
                        fnmatch.fnmatch(item_name, pat)
                        for pat in FALLBACK_IGNORE_FILES
                        if "*" in pat
                    ):
                        is_fallback_ignored = True

                    if (
                        not is_fallback_ignored
                        and item_name.startswith(".")
                        and item_name
                        not in [".gitignore", ".gitattributes", ".gitmodules"]
                    ):
                        is_fallback_ignored = True

                    if is_fallback_ignored:
                        continue

                    main_files.append(str(item_path.resolve()))

                    try:
                        relative_display_path = item_path.relative_to(folder_path_obj)
                    except ValueError:
                        relative_display_path = item_path

                    display_lines.append(
                        f"- {str(relative_display_path).replace(os.sep, '/')}"
                    )
                    found_any = True

            if not found_any:
                display_lines.append(
                    "(No files found or all files ignored in this folder and its subdirectories)"
                )
        except Exception as e:
            logging.error(
                f"Error in _repopulate_main_files_task for {folder_path_obj}: {e}",
                exc_info=True,
            )
            display_lines.append(f"[Error accessing folder: {e}]")
        return main_files, "\n".join(display_lines)

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
            filter_status = ["dot-folders always ignored"]
            if (
                use_gitignore_val
                and not self.use_gitignore_checkbox.cget("state") == "disabled"
            ):
                filter_status.append(".gitignore active")
            if custom_patterns_list:
                filter_status.append("custom ignores active")
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
                relative_path_str = file_p.name

                if project_root_path_obj:
                    try:
                        abs_file_p = file_p.resolve()
                        abs_project_root = project_root_path_obj.resolve()
                        if abs_file_p.is_relative_to(abs_project_root):
                            relative_path_str = str(
                                abs_file_p.relative_to(abs_project_root)
                            )
                        else:
                            candidate_rel_path = os.path.relpath(
                                abs_file_p, abs_project_root
                            )
                            relative_path_str = candidate_rel_path

                    except ValueError:
                        relative_path_str = file_p.name
                    except Exception:
                        relative_path_str = file_p.name

                prompt_parts.append(
                    f"--- File: {relative_path_str.replace(os.sep, '/')} ---"
                )
                prompt_parts.append(read_file_content(file_path_str).strip())
                prompt_parts.append("--- End File ---")
            prompt_parts.append("\n")
        elif self.main_files_source_mode == "Folder" and not main_file_paths_list:
            prompt_parts.extend(
                [
                    "--- MAIN FILE(S) CONTENT ---",
                    "(No main files selected or all files were filtered out from the selected folder.)\n",
                ]
            )
        elif self.main_files_source_mode == "Individual" and not main_file_paths_list:
            prompt_parts.extend(
                ["--- MAIN FILE(S) CONTENT ---", "(No individual main files picked.)\n"]
            )
        return "\n".join(prompt_parts).strip()

    def _update_file_tree_ui(self, tree_string):
        logging.debug("UI Update: Setting file tree content.")
        self._set_textbox_content(
            self.file_tree_textbox,
            tree_string if tree_string else "(No files to display or all ignored)",
        )
        if hasattr(self, "_chain_step") and self._chain_step == "file_tree_done":
            self._orchestrate_full_refresh_step2()

    def _update_main_files_folder_ui(self, result_tuple):
        main_files, display_text = result_tuple
        logging.debug("UI Update: Setting main files folder content.")
        self.main_file_paths = main_files
        self._set_textbox_content(self.selected_main_files_textbox, display_text)
        if hasattr(self, "_chain_step") and self._chain_step == "main_files_done":
            self._orchestrate_full_refresh_step3()

    def _update_final_prompt_ui(self, prompt_string):
        logging.debug("UI Update: Setting final prompt content.")
        self._set_textbox_content(self.final_prompt_textbox, prompt_string)
        if hasattr(self, "_chain_step") and self._chain_step == "prompt_done":
            del self._chain_step

    def _orchestrate_full_refresh(self):
        if self.active_background_tasks > 0:
            logging.warning("Orchestrator: App busy, full refresh deferred.")
            return
        if not self.project_folder_path:
            self._set_textbox_content(self.file_tree_textbox, "")
            self.main_file_paths = []
            self._set_textbox_content(self.selected_main_files_textbox, "")
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
            str(self.project_folder_path),
            self.gitignore_matcher,
            self.use_gitignore_var.get(),
            custom_patterns,
            self.project_folder_path,
        )

    def _orchestrate_full_refresh_step2(self):
        if self.progress_popup and self.progress_popup.winfo_exists():
            self.progress_popup.update_label("Processing main files...")
            self.progress_popup.update_progress(0.5)
        if (
            self.main_files_source_mode == "Folder"
            and self.main_files_selected_folder_path
        ):
            logging.debug("Orchestrator: Step 2 (Main Files Folder)")
            self._chain_step = "main_files_done"
            custom_patterns = self._get_custom_ignore_patterns()
            self._submit_task(
                self._repopulate_main_files_task,
                self._update_main_files_folder_ui,
                self.main_files_selected_folder_path,
                self.gitignore_matcher,
                self.use_gitignore_var.get(),
                custom_patterns,
                self.project_folder_path,
            )
        else:
            logging.debug(
                "Orchestrator: Skipping Step 2, proceeding to Step 3 (Prompt Gen)"
            )
            self._orchestrate_full_refresh_step3()

    def _orchestrate_full_refresh_step3(self):
        if self.progress_popup and self.progress_popup.winfo_exists():
            self.progress_popup.update_label("Generating final prompt...")
            self.progress_popup.update_progress(0.8)
        logging.debug("Orchestrator: Step 3 (Generate Prompt)")
        self._chain_step = "prompt_done"
        self.trigger_generate_prompt_stand_alone(is_part_of_chain=True)
        if self.progress_popup and self.progress_popup.winfo_exists():
            self.progress_popup.update_progress(1.0)

    def trigger_generate_prompt_stand_alone(self, event=None, is_part_of_chain=False):
        if not is_part_of_chain and self.active_background_tasks > 0:
            logging.warning("Trigger Prompt: App busy, request deferred.")
            return
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
            from gitignore_parser import parse_gitignore

            if self.project_folder_path and self.use_gitignore_var.get():
                gitignore_file_path = Path(self.project_folder_path) / ".gitignore"
                if gitignore_file_path.exists() and gitignore_file_path.is_file():
                    try:
                        self.gitignore_matcher = parse_gitignore(
                            str(gitignore_file_path),
                            base_dir=str(self.project_folder_path),
                        )
                        logging.info(f".gitignore loaded from {gitignore_file_path}")
                    except Exception as e:
                        logging.error(f"Error parsing .gitignore: {e}")
                else:
                    logging.info(f".gitignore not found in {self.project_folder_path}")
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
            self.project_folder_path = Path(folder_path_str)
            self.title(f"LLM Prompt Initializer - {self.project_folder_path.name}")
            logging.info(f"Project folder selected: {self.project_folder_path}")
            self.main_file_paths = []
            self.main_files_selected_folder_path = None
            self._set_textbox_content(self.selected_main_files_textbox, "")
            self._orchestrate_full_refresh()
        else:
            logging.info("Project folder selection cancelled.")
            if not self.project_folder_path:
                self._orchestrate_full_refresh()

    def update_main_files_ui(self, selected_mode):
        logging.debug(f"Main files UI switched to: {selected_mode}")
        self.main_files_source_mode = selected_mode
        self.select_main_folder_button.grid_forget()
        self.select_individual_files_button.grid_forget()
        if selected_mode == "Folder":
            self.select_main_folder_button.grid(row=0, column=0, sticky="ew")
        else:
            self.select_individual_files_button.grid(row=0, column=0, sticky="ew")

        if self.main_file_paths or self.main_files_selected_folder_path:
            self.main_file_paths = []
            self.main_files_selected_folder_path = None
            self._set_textbox_content(self.selected_main_files_textbox, "")
            self.trigger_generate_prompt_stand_alone()

    def select_main_files_folder(self):
        logging.debug("Selecting main files folder...")
        if not self.project_folder_path:
            CTkMessagebox(
                title="No Project",
                message="Please open a project folder first (for context like .gitignore and project-relative ignores).",
                icon="warning",
            )
            return

        start_dir = str(self.project_folder_path) if self.project_folder_path else None
        folder_path_str = filedialog.askdirectory(
            title="Select Folder Containing Main Files (will include subdirectories)",
            initialdir=start_dir,
        )
        if folder_path_str:
            self.main_files_selected_folder_path = Path(folder_path_str)
            logging.info(
                f"Main files folder selected: {self.main_files_selected_folder_path}"
            )
            if hasattr(self, "_chain_step"):
                del self._chain_step
            self._orchestrate_full_refresh_step2()

    def select_individual_main_files(self):
        logging.debug("Selecting individual main files...")
        if not self.project_folder_path:
            CTkMessagebox(
                title="No Project",
                message="Please open a project folder first.",
                icon="warning",
            )
            return

        start_dir = str(self.project_folder_path) if self.project_folder_path else None
        file_paths_tuple = filedialog.askopenfilenames(
            title="Select Individual Main Files",
            initialdir=start_dir,
        )
        if file_paths_tuple:
            self.main_file_paths = [str(Path(p).resolve()) for p in file_paths_tuple]
            logging.info(f"Individual main files selected: {self.main_file_paths}")
            self.main_files_selected_folder_path = None

            display_lines = ["Selected Individual Files:"]
            for p_str in self.main_file_paths:
                p_obj = Path(p_str)
                display_name = p_obj.name
                if self.project_folder_path:
                    try:
                        abs_p_obj = p_obj.resolve()
                        abs_proj_root = self.project_folder_path.resolve()
                        if abs_p_obj.is_relative_to(abs_proj_root):
                            display_name = str(abs_p_obj.relative_to(abs_proj_root))
                    except:
                        pass
                display_lines.append(f"- {display_name.replace(os.sep, '/')}")

            self._set_textbox_content(
                self.selected_main_files_textbox, "\n".join(display_lines)
            )
            self.trigger_generate_prompt_stand_alone()

    def copy_prompt(self):
        prompt_text = self.final_prompt_textbox.get("1.0", "end-1c")
        if not prompt_text:
            CTkMessagebox(
                title="Copy Prompt",
                message="Nothing to copy. Generate a prompt first.",
                icon="info",
            )
            return
        try:
            pyperclip.copy(prompt_text)
            CTkMessagebox(
                title="Copy Prompt", message="Prompt copied to clipboard!", icon="check"
            )
        except Exception as e:
            logging.error(f"Copy prompt error: {e}")
            CTkMessagebox(
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
        window_width = 600
        window_height = 400

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.config_toplevel.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.config_toplevel.grab_set()
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

        current_selection = self.config_listbox.get()
        self.config_listbox.delete("all")

        if not self.config_dir.exists():
            return

        for i, f_path in enumerate(sorted(self.config_dir.glob("*.ini"))):
            self.config_listbox.insert(i, f_path.stem)

        if current_selection:
            try:
                all_items = [
                    self.config_listbox.get(i)
                    for i in range(self.config_listbox.size())
                ]
                if current_selection in all_items:
                    idx = all_items.index(current_selection)
                    self.config_listbox.select(idx)
            except Exception:
                pass

    def _save_current_config(self):
        name = self.config_name_entry.get().strip()
        if not name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="Configuration name cannot be empty.",
                icon="cancel",
            )
            return
        if not all(c.isalnum() or c in ["_", "-"] for c in name):
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="Name can only contain letters, numbers, underscores, or hyphens.",
                icon="cancel",
            )
            return

        config = configparser.ConfigParser()
        instructions = self.instructions_textbox.get("1.0", "end-1c")
        custom_ignores = self.custom_ignore_textbox.get("1.0", "end-1c")
        config["Settings"] = {
            "Instructions": instructions,
            "CustomIgnores": custom_ignores,
        }

        file_path = self.config_dir / f"{name}.ini"
        try:
            with open(file_path, "w", encoding="utf-8") as configfile:
                config.write(configfile)
            logging.info(f"Saved configuration: {name}")
            CTkMessagebox(
                master=self.config_toplevel,
                title="Success",
                message=f"Configuration '{name}' saved.",
                icon="check",
            )
            self._populate_config_listbox()
        except Exception as e:
            logging.error(f"Error saving configuration '{name}': {e}")
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message=f"Failed to save configuration: {e}",
                icon="cancel",
            )

    def _load_selected_config(self):
        selected_name_from_list = None
        if hasattr(self, "config_listbox") and self.config_listbox.winfo_exists():
            selected_name_from_list = self.config_listbox.get()

        selected_name_from_entry = self.config_name_entry.get().strip()

        selected_name = None
        if selected_name_from_list:
            selected_name = selected_name_from_list
        elif selected_name_from_entry:
            selected_name = selected_name_from_entry

        if not selected_name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="No configuration selected or named to load.",
                icon="warning",
            )
            return

        file_path = self.config_dir / f"{selected_name}.ini"
        if not file_path.exists():
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message=f"Configuration file for '{selected_name}' not found.",
                icon="cancel",
            )
            return

        config = configparser.ConfigParser()
        try:
            config.read(file_path, encoding="utf-8")
            instructions = config.get("Settings", "Instructions", fallback="")
            custom_ignores = config.get("Settings", "CustomIgnores", fallback="")

            self._set_textbox_content(self.instructions_textbox, instructions)
            self._set_textbox_content(self.custom_ignore_textbox, custom_ignores)

            logging.info(f"Loaded configuration: {selected_name}")

            self._close_config_manager()

            CTkMessagebox(
                master=self,
                title="Success",
                message=f"Configuration '{selected_name}' loaded.",
                icon="check",
            )

            self._debounced_refresh_all_views_and_prompt()
        except Exception as e:
            logging.error(f"Error loading configuration '{selected_name}': {e}")
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message=f"Failed to load configuration: {e}",
                icon="cancel",
            )

    def _delete_selected_config(self):
        selected_name_from_list = None
        if hasattr(self, "config_listbox") and self.config_listbox.winfo_exists():
            selected_name_from_list = self.config_listbox.get()

        selected_name_from_entry = self.config_name_entry.get().strip()

        selected_name = None
        if selected_name_from_list:
            selected_name = selected_name_from_list
        elif selected_name_from_entry:
            selected_name = selected_name_from_entry

        if not selected_name:
            CTkMessagebox(
                master=self.config_toplevel,
                title="Error",
                message="No configuration selected or named to delete.",
                icon="warning",
            )
            return

        msg = CTkMessagebox(
            master=self.config_toplevel,
            title="Confirm Delete",
            message=f"Are you sure you want to delete configuration '{selected_name}'?",
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
                    message=f"Configuration '{selected_name}' deleted.",
                    icon="check",
                )
                if self.config_name_entry.get().strip() == selected_name:
                    self.config_name_entry.delete(0, "end")
                self._populate_config_listbox()
            except Exception as e:
                logging.error(f"Error deleting configuration '{selected_name}': {e}")
                CTkMessagebox(
                    master=self.config_toplevel,
                    title="Error",
                    message=f"Failed to delete configuration: {e}",
                    icon="cancel",
                )


if __name__ == "__main__":
    app = LLMPromptApp()
    app.mainloop()
