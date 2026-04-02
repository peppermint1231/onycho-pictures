"""무좀 사진 자동 분류 - GUI (tkinter + 드래그앤드롭 + 리뷰)"""
import os
import re
import shutil
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

from config import (
    INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, SUPPORTED_EXTENSIONS,
    CROP_TOP, CROP_BOTTOM, CROP_LEFT, CROP_RIGHT,
)
from ocr_engine import get_text_lines
from parser import (
    parse_photo, PhotoInfo, parse_date, parse_name_visit,
    save_learned, group_consecutive_photos,
)
from file_manager import move_photo, move_to_review

# --- 하늘색 다크 테마 ---
COLORS = {
    "bg": "#0f172a",       "surface": "#1e293b",    "surface2": "#273548",
    "accent": "#0ea5e9",   "accent_hover": "#0284c7", "accent_light": "#7dd3fc",
    "text": "#e2e8f0",     "text_dim": "#94a3b8",   "text_muted": "#64748b",
    "success": "#22c55e",  "warning": "#f59e0b",    "error": "#ef4444",
    "border": "#334155",   "drop_bg": "#1e293b",    "drop_hover": "#1e3a5f",
    "table_bg": "#1a2332",
}


def _open_folder(path):
    """탐색기로 폴더를 엽니다."""
    path = os.path.normpath(path)
    target = path if os.path.isdir(path) else os.path.dirname(path)
    if os.path.isdir(target):
        os.startfile(target)


def _open_file(path):
    """기본 프로그램으로 파일을 엽니다."""
    path = os.path.normpath(path)
    if os.path.isfile(path):
        os.startfile(path)


def _make_status(info):
    """PhotoInfo에서 상태 문자열을 생성합니다."""
    visit_str = info.visit_raw or f"{info.visit_number}회"
    tag = " [보정]" if info.corrected else ""
    return f"-> {info.date_raw} {info.patient_name} {visit_str}{tag}"


def _make_dark_button(parent, text, command, **kwargs):
    """다크 테마 버튼을 생성합니다."""
    defaults = dict(
        bg=COLORS["surface2"], fg=COLORS["text"],
        activebackground=COLORS["accent"], activeforeground="white",
        relief="flat", font=("Segoe UI", 9), cursor="hand2", bd=0,
        padx=10, pady=5,
    )
    defaults.update(kwargs)
    return tk.Button(parent, text=text, command=command, **defaults)


# ============================================================
# ReviewDialog: 실패 사진 수기 입력
# ============================================================

class ReviewDialog:
    def __init__(self, parent, fail_items):
        self.parent = parent
        self.fail_items = fail_items
        self.results = {}
        self.current_index = 0

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("수동 분류 - 실패 사진 리뷰")
        self.dialog.geometry("750x580")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()
        self.dialog.configure(bg=COLORS["bg"])

        self._build_ui()
        self._show_current()

    def _build_ui(self):
        main = tk.Frame(self.dialog, bg=COLORS["bg"], padx=15, pady=15)
        main.pack(fill=tk.BOTH, expand=True)

        # 진행 상황
        self.progress_label = tk.Label(
            main, text="", font=("Segoe UI", 11, "bold"),
            bg=COLORS["bg"], fg=COLORS["accent_light"])
        self.progress_label.pack(anchor=tk.W, pady=(0, 12))

        mid = tk.Frame(main, bg=COLORS["bg"])
        mid.pack(fill=tk.BOTH, expand=True)

        # 왼쪽: 크롭 이미지
        img_frame = tk.LabelFrame(mid, text=" OCR 스캔 영역 ", font=("Segoe UI", 9),
                                   bg=COLORS["surface"], fg=COLORS["text_dim"],
                                   bd=1, relief="solid", padx=8, pady=8)
        img_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))
        self.img_label = tk.Label(img_frame, bg=COLORS["surface2"])
        self.img_label.pack(fill=tk.BOTH, expand=True)

        # 오른쪽: 입력 필드
        input_frame = tk.LabelFrame(mid, text=" 수동 입력 ", font=("Segoe UI", 9),
                                     bg=COLORS["surface"], fg=COLORS["text_dim"],
                                     bd=1, relief="solid", padx=12, pady=12)
        input_frame.pack(side=tk.RIGHT, fill=tk.Y)

        # 파일명 / 실패 사유
        for label, var_name, fg in [("파일명:", "filename_var", COLORS["text"]),
                                     ("실패 사유:", "reason_var", COLORS["error"])]:
            tk.Label(input_frame, text=label, bg=COLORS["surface"],
                     fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack(anchor=tk.W, pady=(0, 2))
            var = tk.StringVar()
            setattr(self, var_name, var)
            tk.Label(input_frame, textvariable=var, font=("Segoe UI", 8),
                     bg=COLORS["surface"], fg=fg, wraplength=200).pack(anchor=tk.W, pady=(0, 10))

        # 입력 필드 + OCR 버튼
        fill_cmds = [self._fill_date_from_ocr, self._fill_name_from_ocr, self._fill_visit_from_ocr]
        for (label_text, attr_name), fill_cmd in zip([
            ("날짜 (YYMMDD):", "date_entry"),
            ("환자 이름:", "name_entry"),
            ("회차 (숫자 또는 fu숫자):", "visit_entry"),
        ], fill_cmds):
            row = tk.Frame(input_frame, bg=COLORS["surface"])
            row.pack(fill=tk.X, pady=(0, 2))
            tk.Label(row, text=label_text, bg=COLORS["surface"],
                     fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
            tk.Button(row, text="OCR", command=fill_cmd, bg=COLORS["accent"], fg="white",
                      activebackground=COLORS["accent_hover"], activeforeground="white",
                      relief="flat", font=("Segoe UI", 7, "bold"), cursor="hand2",
                      bd=0, padx=6, pady=1).pack(side=tk.RIGHT)

            entry = tk.Entry(input_frame, width=20, font=("Segoe UI", 12),
                             bg=COLORS["surface2"], fg=COLORS["text"],
                             insertbackground=COLORS["text"], relief="flat",
                             bd=0, highlightthickness=1, highlightcolor=COLORS["accent"],
                             highlightbackground=COLORS["border"])
            entry.pack(anchor=tk.W, pady=(0, 8), ipady=4, fill=tk.X)
            setattr(self, attr_name, entry)

        # 액션 버튼
        btn_frame = tk.Frame(input_frame, bg=COLORS["surface"])
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        for text, cmd in [("저장 & 다음", self._save_and_next),
                           ("건너뛰기", self._skip),
                           ("모두 건너뛰기", self._skip_all)]:
            _make_dark_button(btn_frame, text, cmd).pack(fill=tk.X, pady=2)

        # 네비게이션
        nav = tk.Frame(main, bg=COLORS["bg"])
        nav.pack(fill=tk.X, pady=(12, 0))
        self.btn_prev = _make_dark_button(nav, "< 이전", self._prev, padx=12, pady=4)
        self.btn_prev.pack(side=tk.LEFT)
        _make_dark_button(nav, "리뷰 완료", self._done,
                          bg=COLORS["accent"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=16, pady=4).pack(side=tk.RIGHT)

        self.dialog.bind("<Return>", lambda e: self._save_and_next())

    def _get_cropped_image(self, image_path):
        img = Image.open(image_path)
        w, h = img.size
        if CROP_TOP is not None:
            img = img.crop((int(w * CROP_LEFT), int(h * CROP_TOP),
                            int(w * CROP_RIGHT), int(h * CROP_BOTTOM)))
        return img

    def _show_current(self):
        if self.current_index >= len(self.fail_items):
            self._done()
            return

        item = self.fail_items[self.current_index]
        path = item["path"]

        self.progress_label.config(
            text=f"실패 사진 리뷰: {self.current_index + 1} / {len(self.fail_items)}")
        self.filename_var.set(item["filename"])
        self.reason_var.set(item["reason"])

        # 이전 입력 복원 또는 초기화
        if path in self.results and self.results[path] is not None:
            info = self.results[path]
            for entry, val in [(self.date_entry, info.date_raw),
                                (self.name_entry, info.patient_name),
                                (self.visit_entry, info.visit_raw or str(info.visit_number))]:
                entry.delete(0, tk.END)
                entry.insert(0, val)
        else:
            for entry in (self.date_entry, self.name_entry, self.visit_entry):
                entry.delete(0, tk.END)

        # 크롭 이미지 표시
        try:
            crop_img = self._get_cropped_image(path)
            display_w = 350
            ratio = display_w / crop_img.width
            display_h = int(crop_img.height * ratio)
            if display_h > 400:
                display_h = 400
                ratio = display_h / crop_img.height
                display_w = int(crop_img.width * ratio)
            crop_img = crop_img.resize((display_w, display_h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(crop_img)
            self.img_label.config(image=self._photo)
        except Exception as e:
            self.img_label.config(image="", text=f"이미지 로드 실패:\n{e}", fg=COLORS["error"])

        self.date_entry.focus_set()
        self.btn_prev.config(state="normal" if self.current_index > 0 else "disabled")

    def _get_ocr_text(self):
        reason = self.fail_items[self.current_index]["reason"]
        for prefix in ("파싱 실패 - OCR:", "파싱 실패:"):
            if prefix in reason:
                return reason.split(prefix)[-1].strip()
        return reason

    def _fill_date_from_ocr(self):
        match = re.search(r'(\d{6})', self._get_ocr_text())
        if match:
            self.date_entry.delete(0, tk.END)
            self.date_entry.insert(0, match.group(1))
        self.date_entry.focus_set()

    def _fill_name_from_ocr(self):
        ocr_text = self._get_ocr_text()
        result = parse_name_visit(ocr_text)
        name = result[0] if result else None
        if not name:
            m = re.search(r'([가-힣]{2,5})', ocr_text)
            name = m.group(1) if m else None
        if name:
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, name)
        self.name_entry.focus_set()

    def _fill_visit_from_ocr(self):
        ocr_text = self._get_ocr_text()
        result = parse_name_visit(ocr_text)
        if result:
            self.visit_entry.delete(0, tk.END)
            self.visit_entry.insert(0, str(result[1]))
        else:
            match = re.search(r'(\d{1,3})\s*[회희휘획]', ocr_text)
            if match:
                self.visit_entry.delete(0, tk.END)
                self.visit_entry.insert(0, match.group(1))
        self.visit_entry.focus_set()

    def _save_and_next(self):
        date_raw = self.date_entry.get().strip()
        name = self.name_entry.get().strip()
        visit = self.visit_entry.get().strip()

        if not date_raw or not name or not visit:
            messagebox.showwarning("입력 부족", "날짜, 이름, 회차를 모두 입력해주세요.", parent=self.dialog)
            return
        if len(date_raw) != 6 or not date_raw.isdigit():
            messagebox.showwarning("형식 오류", "날짜는 YYMMDD (6자리 숫자) 형식으로 입력해주세요.", parent=self.dialog)
            return

        fu_match = re.match(r'^[fF][uU]\s*(\d{1,3})$', visit)
        if fu_match:
            visit_number, visit_raw = int(fu_match.group(1)), f"fu{int(fu_match.group(1))}"
        elif visit.isdigit():
            visit_number, visit_raw = int(visit), f"{int(visit)}회"
        else:
            messagebox.showwarning("형식 오류", "회차는 숫자 또는 fu숫자 형식으로 입력해주세요.", parent=self.dialog)
            return

        yy, mm, dd = int(date_raw[0:2]), int(date_raw[2:4]), int(date_raw[4:6])
        if mm < 1 or mm > 12 or dd < 1 or dd > 31:
            messagebox.showwarning("형식 오류", "유효하지 않은 날짜입니다.", parent=self.dialog)
            return

        path = self.fail_items[self.current_index]["path"]
        info = PhotoInfo(
            date_raw=date_raw, year=2000 + yy, month=mm, day=dd,
            patient_name=name, visit_number=visit_number,
            confidence=1.0, source_path=path, visit_raw=visit_raw,
        )
        self.results[path] = info

        # 학습 저장
        save_learned(self._get_ocr_text(), info)

        self.current_index += 1
        self._show_current()

    def _skip(self):
        self.results[self.fail_items[self.current_index]["path"]] = None
        self.current_index += 1
        self._show_current()

    def _skip_all(self):
        for i in range(self.current_index, len(self.fail_items)):
            self.results.setdefault(self.fail_items[i]["path"], None)
        self._done()

    def _prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._show_current()

    def _done(self):
        if self.current_index < len(self.fail_items):
            if any(e.get().strip() for e in (self.date_entry, self.name_entry, self.visit_entry)):
                if messagebox.askyesno("확인", "현재 입력한 내용을 저장하시겠습니까?", parent=self.dialog):
                    self._save_and_next()
                    return
        for item in self.fail_items:
            self.results.setdefault(item["path"], None)
        self.dialog.destroy()

    def get_results(self):
        return self.results


# ============================================================
# LearnedCorrectionsDialog: 학습 보정 관리
# ============================================================

class LearnedCorrectionsDialog:
    def __init__(self, parent):
        from parser import _load_learned, _LEARN_FILE
        self._LEARN_FILE = _LEARN_FILE
        self.data = {k: dict(v) for k, v in _load_learned().items()}

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("학습 보정 관리")
        self.dialog.geometry("600x500")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()
        self.dialog.configure(bg=COLORS["bg"])
        self._build_ui()

    def _build_ui(self):
        main = tk.Frame(self.dialog, bg=COLORS["bg"], padx=15, pady=15)
        main.pack(fill=tk.BOTH, expand=True)

        tk.Label(main, text="학습된 보정 목록", font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main, text="리뷰에서 수정한 결과가 학습됩니다. 선택 후 삭제하세요.",
                 font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["text_dim"]).pack(anchor=tk.W, pady=(0, 12))

        # 테이블
        table_frame = tk.Frame(main, bg=COLORS["bg"])
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("유형", "OCR 인식값", "보정값")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                  height=15, selectmode="extended")
        for col, w in [("유형", 80), ("OCR 인식값", 200), ("보정값", 200)]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=60)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 우클릭 + Delete 키
        self.ctx_menu = tk.Menu(self.dialog, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"],
                                activebackground=COLORS["accent"], activeforeground="white", font=("Segoe UI", 9))
        self.ctx_menu.add_command(label="선택 항목 삭제", command=self._delete_selected)
        self.ctx_menu.add_command(label="전체 초기화", command=self._clear_all)
        self.tree.bind("<Button-3>", self._show_ctx_menu)
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        self._populate()

        # 하단 버튼
        btn_frame = tk.Frame(main, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        _make_dark_button(btn_frame, "선택 항목 삭제", self._delete_selected,
                          bg=COLORS["error"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=12).pack(side=tk.LEFT, padx=(0, 8))
        _make_dark_button(btn_frame, "전체 초기화", self._clear_all, padx=12).pack(side=tk.LEFT)
        _make_dark_button(btn_frame, "닫기", self.dialog.destroy, padx=12).pack(side=tk.RIGHT)

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        for key, label in [("name_corrections", "이름"), ("visit_corrections", "회차"), ("date_corrections", "날짜")]:
            for ocr_val, corrected_val in self.data.get(key, {}).items():
                self.tree.insert("", tk.END, values=(label, ocr_val, corrected_val), tags=(key,))

    def _show_ctx_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_add(row)
        self.ctx_menu.post(event.x_root, event.y_root)

    def _delete_selected(self):
        for item_id in self.tree.selection():
            tags = self.tree.item(item_id)["tags"]
            ocr_val = str(self.tree.item(item_id)["values"][1])
            if tags and tags[0] in self.data:
                self.data[tags[0]].pop(ocr_val, None)
            self.tree.delete(item_id)
        self._save()

    def _clear_all(self):
        if messagebox.askyesno("확인", "모든 학습 데이터를 삭제하시겠습니까?", parent=self.dialog):
            self.data = {"name_corrections": {}, "visit_corrections": {}, "date_corrections": {}}
            self._populate()
            self._save()

    def _save(self):
        import json
        import parser as parser_module
        try:
            with open(self._LEARN_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            parser_module._learned_data = self.data
        except Exception:
            pass


# ============================================================
# OrganizerApp: 메인 GUI
# ============================================================

class OrganizerApp:
    def __init__(self):
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title("무좀 사진 자동 분류기")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        self.root.configure(bg=COLORS["bg"])

        # 상태 변수
        self.input_dir = tk.StringVar(value=INPUT_DIR)
        self.output_dir = tk.StringVar(value=OUTPUT_DIR)
        self.review_dir = tk.StringVar(value=REVIEW_DIR)
        self.copy_mode = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="준비 완료")
        self.file_count_var = tk.StringVar()

        # 처리 상태
        self.is_running = False
        self.is_paused = False
        self.pause_event = threading.Event()
        self.pause_event.set()
        self._abort_processing = False
        self._run_after_abort = False
        self.avg_time_per_image = None

        # 미리보기 캐시
        self.cached_results = {}
        self.cached_fail_items = []
        self.cache_valid = False

        self._setup_style()
        self._build_ui()

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                         fieldbackground=COLORS["surface2"], font=("Segoe UI", 9))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("TLabelframe", background=COLORS["surface"], foreground=COLORS["text_dim"],
                         bordercolor=COLORS["border"])
        style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["accent_light"],
                         font=("Segoe UI", 9, "bold"))
        style.configure("TEntry", fieldbackground=COLORS["surface2"], foreground=COLORS["text"],
                         insertcolor=COLORS["text"], bordercolor=COLORS["border"])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])

        style.configure("TButton", background=COLORS["surface2"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"], focuscolor=COLORS["accent"],
                         font=("Segoe UI", 9), padding=(12, 6))
        style.map("TButton", background=[("active", COLORS["accent"]), ("pressed", COLORS["accent_hover"])],
                   foreground=[("active", "white")])

        style.configure("Accent.TButton", background=COLORS["accent"], foreground="white",
                         font=("Segoe UI", 10, "bold"), padding=(16, 8))
        style.map("Accent.TButton", background=[("active", COLORS["accent_hover"])])

        style.configure("TProgressbar", background=COLORS["accent"], troughcolor=COLORS["surface2"],
                         bordercolor=COLORS["border"], lightcolor=COLORS["accent"], darkcolor=COLORS["accent"])

        style.configure("Treeview", background=COLORS["table_bg"], foreground=COLORS["text"],
                         fieldbackground=COLORS["table_bg"], bordercolor=COLORS["border"],
                         font=("Segoe UI", 9), rowheight=28)
        style.configure("Treeview.Heading", background=COLORS["surface2"], foreground=COLORS["text"],
                         font=("Segoe UI", 9, "bold"), bordercolor=COLORS["border"])
        style.map("Treeview", background=[("selected", COLORS["accent"])], foreground=[("selected", "white")])

        style.configure("Vertical.TScrollbar", background=COLORS["surface2"], troughcolor=COLORS["bg"],
                         bordercolor=COLORS["border"], arrowcolor=COLORS["text_dim"])
        style.map("Vertical.TScrollbar", background=[("active", COLORS["accent"])])

    def _build_ui(self):
        main = tk.Frame(self.root, bg=COLORS["bg"], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)

        # 타이틀
        title_frame = tk.Frame(main, bg=COLORS["bg"])
        title_frame.pack(fill=tk.X, pady=(0, 18))
        tk.Label(title_frame, text="무좀 사진 자동 분류기", font=("Segoe UI", 20, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side=tk.LEFT)
        tk.Label(title_frame, text="OCR Photo Organizer", font=("Segoe UI", 10),
                 bg=COLORS["bg"], fg=COLORS["text_muted"]).pack(side=tk.LEFT, padx=(12, 0), pady=(8, 0))

        # 폴더 설정
        folder_frame = tk.LabelFrame(main, text=" 폴더 설정 ", font=("Segoe UI", 9, "bold"),
                                      bg=COLORS["surface"], fg=COLORS["accent_light"],
                                      bd=1, relief="solid", padx=12, pady=10)
        folder_frame.pack(fill=tk.X, pady=(0, 12))

        for label_text, var, browse_cmd, open_cmd in [
            ("입력 폴더", self.input_dir, self._browse_input, lambda: _open_folder(self.input_dir.get())),
            ("출력 폴더", self.output_dir, self._browse_output, lambda: _open_folder(self.output_dir.get())),
            ("리뷰 폴더", self.review_dir, self._browse_review, lambda: _open_folder(self.review_dir.get())),
        ]:
            row = tk.Frame(folder_frame, bg=COLORS["surface"])
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label_text, width=10, anchor="w", bg=COLORS["surface"],
                     fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, font=("Segoe UI", 9), bg=COLORS["surface2"],
                     fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
                     bd=0, highlightthickness=1, highlightcolor=COLORS["accent"],
                     highlightbackground=COLORS["border"]).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=3)
            _make_dark_button(row, "열기", open_cmd, font=("Segoe UI", 8),
                              fg=COLORS["text_dim"], padx=8, pady=2).pack(side=tk.RIGHT, padx=(0, 4))
            _make_dark_button(row, "찾아보기", browse_cmd, font=("Segoe UI", 8),
                              fg=COLORS["text_dim"], padx=8, pady=2).pack(side=tk.RIGHT, padx=(0, 4))

        # 드래그 앤 드롭
        self.drop_frame = tk.Label(
            main, text="여기에 사진 파일을 드래그 앤 드롭하세요\n(입력 폴더로 복사됩니다)",
            relief="flat", bg=COLORS["drop_bg"], fg=COLORS["text_dim"],
            font=("Segoe UI", 10), height=3, cursor="hand2",
            highlightthickness=2, highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"])
        self.drop_frame.pack(fill=tk.X, pady=(0, 8))

        if HAS_DND:
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_frame.dnd_bind("<<DragEnter>>", lambda e: self.drop_frame.config(bg=COLORS["drop_hover"], highlightbackground=COLORS["accent"]))
            self.drop_frame.dnd_bind("<<DragLeave>>", lambda e: self.drop_frame.config(bg=COLORS["drop_bg"], highlightbackground=COLORS["border"]))
        else:
            self.drop_frame.config(text="드래그 앤 드롭 미지원\n(찾아보기 버튼을 사용하세요)", fg=COLORS["text_muted"])

        # 파일 수
        tk.Label(main, textvariable=self.file_count_var, font=("Segoe UI", 9, "bold"),
                 bg=COLORS["bg"], fg=COLORS["accent_light"]).pack(anchor=tk.W, pady=(0, 8))
        self._update_file_count()

        # 옵션 + 버튼
        ctrl_frame = tk.Frame(main, bg=COLORS["bg"])
        ctrl_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Checkbutton(ctrl_frame, text="복사 모드 (원본 유지)", variable=self.copy_mode).pack(side=tk.LEFT)
        _make_dark_button(ctrl_frame, "학습 보정 관리", self._open_learned,
                          font=("Segoe UI", 8), fg=COLORS["text_dim"], padx=8, pady=2).pack(side=tk.LEFT, padx=(12, 0))

        # 버튼 (RIGHT pack 역순)
        self.btn_run = ttk.Button(ctrl_frame, text="분류 실행", command=self._run_organize, style="Accent.TButton")
        self.btn_run.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_review = ttk.Button(ctrl_frame, text="실패 사진 리뷰", command=self._open_review, state="disabled")
        self.btn_review.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_pause = ttk.Button(ctrl_frame, text="일시정지", command=self._toggle_pause, state="disabled")
        self.btn_pause.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_preview = ttk.Button(ctrl_frame, text="미리보기", command=self._run_preview)
        self.btn_preview.pack(side=tk.RIGHT)

        # 진행 바 + 상태
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 8))
        tk.Label(main, textvariable=self.status_var, font=("Segoe UI", 9),
                 bg=COLORS["bg"], fg=COLORS["text_dim"]).pack(anchor=tk.W, pady=(0, 4))

        # 결과 테이블
        table_frame = tk.LabelFrame(main, text=" 처리 결과 ", font=("Segoe UI", 9, "bold"),
                                     bg=COLORS["surface"], fg=COLORS["accent_light"],
                                     bd=1, relief="solid", padx=6, pady=6)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        columns = ("파일명", "날짜", "환자명", "회차", "상태")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col, w in [("파일명", 220), ("날짜", 120), ("환자명", 100), ("회차", 80), ("상태", 200)]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=50)
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 우클릭 메뉴
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"],
                                    activebackground=COLORS["accent"], activeforeground="white",
                                    font=("Segoe UI", 9), bd=1, relief="solid")
        self.context_menu.add_command(label="사진 파일 열기", command=self._open_selected_photo)
        self.context_menu.add_command(label="개별 리뷰하기", command=self._review_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="입력 폴더 열기", command=lambda: _open_folder(self.input_dir.get()))
        self.context_menu.add_command(label="출력 폴더 열기", command=lambda: _open_folder(self.output_dir.get()))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="선택 행 복사", command=self._copy_selected_row)
        self.context_menu.add_command(label="전체 결과 복사", command=self._copy_all_rows)

        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Double-1>", lambda e: self._on_double_click())

    # --- 이벤트 핸들러 ---

    def _on_drop(self, event):
        raw = event.data.strip()
        paths = []
        i = 0
        while i < len(raw):
            if raw[i] == '{':
                end = raw.index('}', i)
                paths.append(raw[i+1:end])
                i = end + 2
            elif raw[i] == ' ':
                i += 1
            else:
                end = raw.find(' ', i)
                if end == -1:
                    end = len(raw)
                paths.append(raw[i:end])
                i = end + 1

        input_dir = self.input_dir.get()
        os.makedirs(input_dir, exist_ok=True)
        copied = 0
        for path in paths:
            if not os.path.isfile(path):
                continue
            if os.path.splitext(path)[1].lower() not in SUPPORTED_EXTENSIONS:
                continue
            dest = os.path.join(input_dir, os.path.basename(path))
            if os.path.exists(dest):
                name, ext = os.path.splitext(os.path.basename(path))
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(input_dir, f"{name}_{counter}{ext}")
                    counter += 1
            shutil.copy2(path, dest)
            copied += 1

        self.cache_valid = False
        self._update_file_count()
        self.drop_frame.config(bg=COLORS["drop_bg"], text=f"{copied}장의 사진이 입력 폴더로 복사되었습니다")

    def _show_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        self.context_menu.post(event.x_root, event.y_root)

    def _on_double_click(self):
        selected = self.tree.selection()
        if not selected:
            return
        status = str(self.tree.item(selected[0])["values"][4])
        if any(kw in status for kw in ("실패", "미분류", "오류")):
            self._review_selected()
        else:
            self._open_selected_photo()

    def _open_selected_photo(self):
        selected = self.tree.selection()
        if not selected:
            return
        filename = str(self.tree.item(selected[0])["values"][0])
        for path in self.cached_results:
            if os.path.basename(path) == filename:
                _open_file(path)
                return
        input_path = os.path.join(self.input_dir.get(), filename)
        if os.path.isfile(input_path):
            _open_file(input_path)

    def _copy_selected_row(self):
        selected = self.tree.selection()
        if not selected:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\t".join(str(v) for v in self.tree.item(selected[0])["values"]))

    def _copy_all_rows(self):
        rows = ["\t".join(["파일명", "날짜", "환자명", "회차", "상태"])]
        for item in self.tree.get_children():
            rows.append("\t".join(str(v) for v in self.tree.item(item)["values"]))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(rows))

    def _review_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("알림", "행을 먼저 선택해주세요.")
            return
        filename = str(self.tree.item(selected[0])["values"][0])
        status = str(self.tree.item(selected[0])["values"][4])

        fail_item = next((item for item in self.cached_fail_items if item["filename"] == filename), None)
        if fail_item is None:
            for path, info in self.cached_results.items():
                if os.path.basename(path) == filename:
                    fail_item = {"path": path, "filename": filename, "reason": status if info is None else f"현재: {status}"}
                    break
        if fail_item is None:
            input_path = os.path.join(self.input_dir.get(), filename)
            if os.path.isfile(input_path):
                fail_item = {"path": input_path, "filename": filename, "reason": status}
        if fail_item is None:
            messagebox.showinfo("알림", f"'{filename}' 파일을 찾을 수 없습니다.")
            return

        dialog = ReviewDialog(self.root, [fail_item])
        self.root.wait_window(dialog.dialog)

        for path, info in dialog.get_results().items():
            if info is not None:
                self.cached_results[path] = info
                self.cached_fail_items = [item for item in self.cached_fail_items if item["path"] != path]
                visit_str = info.visit_raw or f"{info.visit_number}회"
                self.tree.item(selected[0], values=(
                    filename, f"{info.year}.{info.month:02d}.{info.day:02d}",
                    info.patient_name, visit_str, _make_status(info)))

        self.btn_review.config(state="normal" if self.cached_fail_items else "disabled")

    # --- 컨트롤 ---

    def _toggle_pause(self):
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            self.btn_pause.config(text="일시정지")
            self._update_status_direct("재개됨...")
        else:
            self.is_paused = True
            self.pause_event.clear()
            self.btn_pause.config(text="재개")
            self.btn_run.config(state="normal")
            self._update_status_direct("일시정지됨 - '재개' 또는 '분류 실행'")

    def _open_learned(self):
        LearnedCorrectionsDialog(self.root)

    def _update_file_count(self):
        count = len(self._scan_images(self.input_dir.get()))
        text = f"입력 폴더 사진: {count}장"
        if count > 0 and self.avg_time_per_image:
            est = int(count * self.avg_time_per_image)
            m, s = divmod(est, 60)
            text += f"  |  예상 처리시간: {f'{m}분 ' if m else ''}{s}초"
        self.file_count_var.set(text)

    def _browse_input(self):
        path = filedialog.askdirectory(title="입력 폴더 선택")
        if path:
            self.input_dir.set(path)
            self.cache_valid = False
            self._update_file_count()

    def _browse_output(self):
        path = filedialog.askdirectory(title="출력 폴더 선택")
        if path:
            self.output_dir.set(path)

    def _browse_review(self):
        path = filedialog.askdirectory(title="리뷰 폴더 선택")
        if path:
            self.review_dir.set(path)

    def _set_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.btn_preview.config(state=state)
        self.btn_run.config(state=state)
        if enabled:
            self.btn_pause.config(state="disabled", text="일시정지")
            self.is_paused = False
            self.pause_event.set()
        else:
            self.btn_pause.config(state="normal")
        self.btn_review.config(state="normal" if enabled and self.cached_fail_items else "disabled")

    def _scan_images(self, input_dir):
        if not os.path.exists(input_dir):
            return []
        return sorted(os.path.join(input_dir, f) for f in os.listdir(input_dir)
                       if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS)

    # --- 리뷰 ---

    def _open_review(self):
        if not self.cached_fail_items:
            messagebox.showinfo("알림", "리뷰할 실패 사진이 없습니다.")
            return
        dialog = ReviewDialog(self.root, self.cached_fail_items)
        self.root.wait_window(dialog.dialog)

        reviewed = 0
        for path, info in dialog.get_results().items():
            if info is not None:
                self.cached_results[path] = info
                reviewed += 1
        self.cached_fail_items = [item for item in self.cached_fail_items
                                   if dialog.get_results().get(item["path"]) is None]
        self._refresh_table()
        if reviewed > 0:
            self._update_status_direct(f"리뷰 완료! {reviewed}장 수기 입력됨  |  '분류 실행' 버튼을 누르면 모두 분류합니다.")
        self.btn_review.config(state="normal" if self.cached_fail_items else "disabled")

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for path, info in self.cached_results.items():
            filename = os.path.basename(path)
            if info is not None:
                vs = info.visit_raw or f"{info.visit_number}회"
                self.tree.insert("", tk.END, values=(
                    filename, f"{info.year}.{info.month:02d}.{info.day:02d}",
                    info.patient_name, vs, _make_status(info)))
            else:
                self.tree.insert("", tk.END, values=(filename, "-", "-", "-", "미분류 (리뷰 필요)"))

    def _update_status_direct(self, text):
        self.status_var.set(text)

    # --- 미리보기 / 분류 실행 ---

    def _run_preview(self):
        self.cache_valid = False
        self.cached_fail_items = []
        self._start_processing(dry_run=True)

    def _run_organize(self):
        if self.is_running and self.is_paused:
            # 일시정지 중 분류실행 → 미리보기 중단 후 자동 분류
            self.is_paused = False
            self.pause_event.set()
            self.cache_valid = True
            self._abort_processing = True
            self._run_after_abort = True  # 중단 후 자동 분류 플래그
            return

        if self.cache_valid and self.cached_results:
            sc = sum(1 for v in self.cached_results.values() if v is not None)
            fc = sum(1 for v in self.cached_results.values() if v is None)
            msg = f"{sc}장을 분류하시겠습니까?"
            if fc > 0:
                msg += f"\n(미분류 {fc}장은 리뷰 폴더로 이동)"
            msg += f"\n\n모드: {'복사' if self.copy_mode.get() else '이동'}\n출력: {self.output_dir.get()}"
            if messagebox.askyesno("확인", msg):
                self._start_processing(dry_run=False, use_cache=True)
            return

        images = self._scan_images(self.input_dir.get())
        if not images:
            messagebox.showwarning("알림", "입력 폴더에 이미지가 없습니다.")
            return
        if messagebox.askyesno("확인",
            f"{len(images)}장의 사진을 분류하시겠습니까?\n\n"
            f"모드: {'복사' if self.copy_mode.get() else '이동'}\n"
            f"입력: {self.input_dir.get()}\n출력: {self.output_dir.get()}"):
            self._start_processing(dry_run=False)

    def _start_processing(self, dry_run, use_cache=False):
        if self.is_running:
            return
        self.is_running = True
        self._set_buttons_enabled(False)
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._process, args=(dry_run, use_cache), daemon=True).start()

    def _process(self, dry_run, use_cache=False):
        output_dir = self.output_dir.get()
        review_dir = self.review_dir.get()
        copy = self.copy_mode.get()

        # === 캐시 사용 모드 ===
        if use_cache and self.cached_results:
            items = list(self.cached_results.items())
            total = len(items)
            self.root.after(0, lambda: self.progress.config(maximum=total, value=0))
            self._update_status(f"[실행] {total}장 분류 중...")
            success = fail = 0

            for i, (image_path, info) in enumerate(items):
                self.pause_event.wait()
                filename = os.path.basename(image_path)
                if info is None:
                    self._add_row(filename, "-", "-", "-", "미분류 -> 리뷰 폴더")
                    move_to_review(image_path, reason="미분류", review_dir=review_dir)
                    fail += 1
                else:
                    vs = info.visit_raw or f"{info.visit_number}회"
                    action = "복사" if copy else "이동"
                    try:
                        move_photo(info, output_dir, copy)
                        self._add_row(filename, f"{info.year}.{info.month:02d}.{info.day:02d}",
                                      info.patient_name, vs, f"{action} 완료")
                        success += 1
                    except Exception as e:
                        self._add_row(filename, "-", "-", "-", f"실패: {e}")
                        fail += 1
                self._update_progress(i + 1)

            self._update_status(f"분류 완료! 성공: {success}장 / 실패: {fail}장")
            self.cache_valid = False
            self.cached_results.clear()
            self.cached_fail_items.clear()
            msg = f"성공: {success}장\n실패: {fail}장" + ("\n\n리뷰 폴더를 확인해주세요." if fail else "")
            self.root.after(0, lambda: messagebox.showinfo("완료", msg))
            self._finish()
            return

        # === 일반 OCR 처리 ===
        images = self._scan_images(self.input_dir.get())
        total = len(images)
        if total == 0:
            self._update_status("입력 폴더에 이미지가 없습니다.")
            self._finish()
            return

        self.root.after(0, lambda: self.progress.config(maximum=total, value=0))
        mode_text = "[미리보기]" if dry_run else "[실행]"
        self._update_status(f"{mode_text} {total}장 처리 중...")

        success = fail = 0
        new_cache = {}
        new_fail_items = []
        start_time = time.time()

        for i, image_path in enumerate(images):
            self.pause_event.wait()
            if self._abort_processing:
                break
            filename = os.path.basename(image_path)

            # 진행률 + ETA
            elapsed = time.time() - start_time
            eta = f" | 남은시간 {int(elapsed / i * (total - i))}초" if i > 0 else ""
            pct = int((i + 1) / total * 100)
            self._update_status(f"{mode_text} ({i+1}/{total}) {pct}%{eta} | {filename}")

            # OCR
            try:
                lines, confidence = get_text_lines(image_path)
            except Exception as e:
                self._add_row(filename, "-", "-", "-", f"OCR 오류: {e}")
                if not dry_run:
                    move_to_review(image_path, reason=f"OCR 오류: {e}", review_dir=review_dir)
                new_cache[image_path] = None
                new_fail_items.append({"path": image_path, "filename": filename, "reason": f"OCR 오류: {e}"})
                fail += 1
                self._update_progress(i + 1)
                continue

            if not lines:
                self._add_row(filename, "-", "-", "-", "텍스트 미발견")
                if not dry_run:
                    move_to_review(image_path, reason="텍스트 미발견", review_dir=review_dir)
                new_cache[image_path] = None
                new_fail_items.append({"path": image_path, "filename": filename, "reason": "텍스트 미발견"})
                fail += 1
                self._update_progress(i + 1)
                continue

            info = parse_photo(lines, confidence, image_path)
            if info is None:
                ocr_text = " / ".join(lines)
                self._add_row(filename, "-", "-", "-", f"파싱 실패: {ocr_text}")
                if not dry_run:
                    move_to_review(image_path, reason=f"파싱 실패 - OCR: {ocr_text}", review_dir=review_dir)
                new_cache[image_path] = None
                new_fail_items.append({"path": image_path, "filename": filename, "reason": f"파싱 실패: {ocr_text}"})
                fail += 1
                self._update_progress(i + 1)
                continue

            vs = info.visit_raw or f"{info.visit_number}회"
            date_str = f"{info.year}.{info.month:02d}.{info.day:02d}"

            if dry_run:
                self._add_row(filename, date_str, info.patient_name, vs, _make_status(info))
                new_cache[image_path] = info
            else:
                action = "복사" if copy else "이동"
                try:
                    move_photo(info, output_dir, copy)
                    self._add_row(filename, date_str, info.patient_name, vs, f"{action} 완료")
                except Exception as e:
                    self._add_row(filename, date_str, info.patient_name, vs, f"실패: {e}")
                    fail += 1
                    self._update_progress(i + 1)
                    continue
            success += 1
            self._update_progress(i + 1)

        # === 연속 촬영 그룹 보완 ===
        if new_fail_items and new_cache:
            for group in group_consecutive_photos(images):
                group_info = next((new_cache[p] for p in group if new_cache.get(p) is not None), None)
                if not group_info:
                    continue
                for path in group:
                    if new_cache.get(path) is not None:
                        continue
                    filled = PhotoInfo(
                        date_raw=group_info.date_raw, year=group_info.year,
                        month=group_info.month, day=group_info.day,
                        patient_name=group_info.patient_name, visit_number=group_info.visit_number,
                        confidence=group_info.confidence, source_path=path,
                        visit_raw=group_info.visit_raw, corrected=group_info.corrected,
                    )
                    new_cache[path] = filled
                    fn = os.path.basename(path)
                    vs = filled.visit_raw or f"{filled.visit_number}회"
                    self._add_row(fn, f"{filled.year}.{filled.month:02d}.{filled.day:02d}",
                                  filled.patient_name, vs,
                                  f"-> {filled.date_raw} {filled.patient_name} {vs} [그룹]")
                    success += 1
                    fail -= 1
            new_fail_items = [item for item in new_fail_items if new_cache.get(item["path"]) is None]

        if dry_run:
            self.cached_results = new_cache
            self.cached_fail_items = new_fail_items
            self.cache_valid = True

        aborted = self._abort_processing
        self._abort_processing = False

        if dry_run:
            if aborted:
                msg = f"미리보기 중단! {success + fail}장 처리됨 (성공: {success} / 실패: {fail})"
                msg += "  |  '분류 실행'으로 분류하거나 '미리보기'로 재개"
            else:
                msg = f"미리보기 완료! 성공: {success}장 / 실패: {fail}장"
                msg += "  |  '실패 사진 리뷰' 버튼으로 수기 입력 가능" if fail else "  |  '분류 실행' 버튼을 누르면 바로 분류합니다."
            self._update_status(msg)
        else:
            self._update_status(f"분류 완료! 성공: {success}장 / 실패: {fail}장")
            result_msg = f"성공: {success}장\n실패: {fail}장" + ("\n\n리뷰 폴더를 확인해주세요." if fail else "")
            self.root.after(0, lambda: messagebox.showinfo("완료", result_msg))

        processed = success + fail
        if processed > 0:
            self.avg_time_per_image = (time.time() - start_time) / processed

        self._finish()

    # --- UI 유틸리티 (스레드 안전) ---

    def _update_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _update_progress(self, value):
        self.root.after(0, lambda: self.progress.config(value=value))

    def _add_row(self, filename, date, name, visit, status):
        self.root.after(0, lambda: self.tree.insert("", tk.END, values=(filename, date, name, visit, status)))

    def _finish(self):
        self.is_running = False
        self.root.after(0, lambda: self._set_buttons_enabled(True))
        self.root.after(0, self._update_file_count)
        # 일시정지 → 분류실행 플래그가 있으면 자동으로 분류 시작
        if self._run_after_abort and self.cache_valid and self.cached_results:
            self._run_after_abort = False
            self.root.after(100, self._run_organize)

    def run(self):
        self.root.mainloop()


def main():
    app = OrganizerApp()
    app.run()


if __name__ == "__main__":
    main()
