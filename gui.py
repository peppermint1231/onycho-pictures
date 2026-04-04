"""무좀 사진 자동 분류 - GUI (tkinter + 드래그앤드롭 + 리뷰)"""
import os
import re
import shutil
from send2trash import send2trash
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
    tags = ""
    if info.corrected:
        tags += " [보정]"
    if info.visit_review:
        tags += " [회차검토]"
    return f"-> {info.date_raw} {info.patient_name} {visit_str}{tags}"


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
    def __init__(self, parent, fail_items, group_paths=None, group_cache=None,
                 all_images=None, cached_results=None):
        self.parent = parent
        self.fail_items = fail_items
        self.results = {}
        self.current_index = 0
        self.group_paths = group_paths or []
        self.group_cache = group_cache or {}
        self.all_images = all_images or []
        self.ext_cached_results = cached_results or {}

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("수동 분류 - 실패 사진 리뷰")
        self.dialog.geometry("750x780")
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
            ("회차 (예: 3, fu2):", "visit_entry"),
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

        # 그룹 일괄 적용 체크박스 + 그룹 정보
        self.apply_group_var = tk.BooleanVar(value=False)
        self.group_check = tk.Checkbutton(
            input_frame, text="그룹 일괄 적용", variable=self.apply_group_var,
            bg=COLORS["surface"], fg=COLORS["accent_light"], selectcolor=COLORS["surface2"],
            activebackground=COLORS["surface"], activeforeground=COLORS["accent_light"],
            font=("Segoe UI", 9), command=self._toggle_group_detail)
        self.group_detail_outer = tk.Frame(input_frame, bg=COLORS["surface2"])
        self.group_detail_canvas = tk.Canvas(
            self.group_detail_outer, bg=COLORS["surface2"], highlightthickness=0,
            height=min(200, max(80, len(self.group_paths) * 20)))
        self.group_detail_scrollbar = tk.Scrollbar(
            self.group_detail_outer, orient=tk.VERTICAL, command=self.group_detail_canvas.yview)
        self.group_detail_frame = tk.Frame(self.group_detail_canvas, bg=COLORS["surface2"], padx=6, pady=4)
        self.group_detail_frame.bind("<Configure>", lambda e: self.group_detail_canvas.configure(
            scrollregion=self.group_detail_canvas.bbox("all")))
        self.group_detail_canvas.create_window((0, 0), window=self.group_detail_frame, anchor=tk.NW)
        self.group_detail_canvas.configure(yscrollcommand=self.group_detail_scrollbar.set)
        self.group_detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.group_detail_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        # 마우스 휠 스크롤
        def _on_mousewheel(event):
            self.group_detail_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.group_detail_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.group_detail_frame.bind("<MouseWheel>", _on_mousewheel)
        if len(self.group_paths) > 1:
            self.group_check.config(
                text=f"그룹 일괄 적용 ({len(self.group_paths)}장)")
            self.group_check.pack(anchor=tk.W, pady=(8, 0))
            self._build_group_detail()

        # 액션 버튼
        btn_frame = tk.Frame(input_frame, bg=COLORS["surface"])
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        for text, cmd in [("저장 & 다음", self._save_and_next),
                           ("건너뛰기", self._skip),
                           ("모두 건너뛰기", self._skip_all)]:
            _make_dark_button(btn_frame, text, cmd).pack(fill=tk.X, pady=2)
        _make_dark_button(btn_frame, "파일 삭제", self._delete_current_file,
                          bg=COLORS["error"], fg="white").pack(fill=tk.X, pady=(8, 2))

        # 네비게이션
        nav = tk.Frame(main, bg=COLORS["bg"])
        nav.pack(fill=tk.X, pady=(12, 0))
        self.btn_prev = _make_dark_button(nav, "< 이전", self._prev, padx=12, pady=4)
        self.btn_prev.pack(side=tk.LEFT)
        _make_dark_button(nav, "리뷰 완료", self._done,
                          bg=COLORS["accent"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=16, pady=4).pack(side=tk.RIGHT)

        self.dialog.bind("<Return>", lambda e: self._save_and_next())
        self.dialog.bind("<Escape>", lambda e: self._done())

    def _build_group_detail(self):
        """그룹 멤버 목록과 파싱 결과를 표시하는 패널 구성"""
        def _on_mousewheel(event):
            self.group_detail_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        for gp in self.group_paths:
            fn = os.path.basename(gp)
            info = self.group_cache.get(gp)
            if info is not None:
                if isinstance(info, dict) and "failed" in info:
                    detail = info["display"]
                    fg = COLORS["error"]
                elif isinstance(info, dict) and "display" in info:
                    detail = info["display"]
                    fg = COLORS["success"]
                elif isinstance(info, dict) and "date" in info:
                    detail = f"{info['date']}  {info['name']}  {info['visit']}"
                    fg = COLORS["success"]
                elif hasattr(info, "patient_name"):
                    vs = info.visit_raw or f"{info.visit_number}회"
                    detail = f"{info.year}.{info.month:02d}.{info.day:02d}  {info.patient_name}  {vs}"
                    fg = COLORS["success"]
                else:
                    detail = "미분류"
                    fg = COLORS["text_muted"]
            else:
                detail = "미분류"
                fg = COLORS["text_muted"]
            row = tk.Frame(self.group_detail_frame, bg=COLORS["surface2"])
            row.pack(fill=tk.X, pady=1)
            row.bind("<MouseWheel>", _on_mousewheel)
            path = gp
            lbl1 = tk.Label(row, text=fn, font=("Segoe UI", 7),
                     bg=COLORS["surface2"], fg=COLORS["text_dim"],
                     width=22, anchor=tk.W, cursor="hand2")
            lbl1.pack(side=tk.LEFT)
            lbl1.bind("<MouseWheel>", _on_mousewheel)
            lbl1.bind("<Double-1>", lambda e, p=path: _open_file(p))
            lbl2 = tk.Label(row, text=detail, font=("Segoe UI", 7),
                     bg=COLORS["surface2"], fg=fg, anchor=tk.W, cursor="hand2")
            lbl2.pack(side=tk.LEFT, padx=(4, 0))
            lbl2.bind("<MouseWheel>", _on_mousewheel)
            lbl2.bind("<Double-1>", lambda e, p=path: _open_file(p))

    def _toggle_group_detail(self):
        """체크박스 토글 시 그룹 상세 패널 표시/숨김"""
        if self.apply_group_var.get():
            self.group_detail_outer.pack(fill=tk.X, pady=(4, 0))
        else:
            self.group_detail_outer.pack_forget()

    def _update_group_for_current(self, path):
        """현재 항목에 맞게 그룹 정보를 동적으로 갱신"""
        # 그룹 찾기
        target = path
        target_fn = os.path.basename(target)
        all_paths = self.all_images
        if target not in all_paths:
            for p in all_paths:
                if os.path.basename(p) == target_fn:
                    target = p
                    break
        self.group_paths = []
        for group in group_consecutive_photos(all_paths):
            if target in group:
                self.group_paths = group
                break

        # 그룹 캐시 구성
        cache_by_name = {os.path.basename(p): info for p, info in self.ext_cached_results.items()} if self.ext_cached_results else {}
        self.group_cache = {}
        for gp in self.group_paths:
            fn = os.path.basename(gp)
            cached = self.ext_cached_results.get(gp)
            if cached is None and fn in cache_by_name:
                cached = cache_by_name[fn]
            self.group_cache[gp] = cached

        # UI 갱신: 기존 그룹 상세 제거 후 재구성
        self.apply_group_var.set(False)
        self.group_detail_outer.pack_forget()
        for w in self.group_detail_frame.winfo_children():
            w.destroy()

        if len(self.group_paths) > 1:
            self.group_check.config(text=f"그룹 일괄 적용 ({len(self.group_paths)}장)")
            self.group_check.pack(anchor=tk.W, pady=(8, 0))
            self._build_group_detail()
            self.group_detail_canvas.config(
                height=min(200, max(80, len(self.group_paths) * 20)))
        else:
            self.group_check.pack_forget()

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

        # 이전 입력 복원 또는 기존 파싱값 채우기
        if path in self.results and self.results[path] is not None:
            info = self.results[path]
            for entry, val in [(self.date_entry, info.date_raw),
                                (self.name_entry, info.patient_name),
                                (self.visit_entry, info.visit_raw or str(info.visit_number))]:
                entry.delete(0, tk.END)
                entry.insert(0, val)
        elif "existing_info" in item:
            info = item["existing_info"]
            visit_val = info.visit_raw or str(info.visit_number)
            visit_val = re.sub(r'회$', '', visit_val)
            for entry, val in [(self.date_entry, info.date_raw),
                                (self.name_entry, info.patient_name),
                                (self.visit_entry, visit_val)]:
                entry.delete(0, tk.END)
                entry.insert(0, val)
        elif "existing_display" in item:
            d = item["existing_display"]
            # 날짜를 YYYY.MM.DD → YYMMDD로 변환
            date_parts = d["date"].split(".")
            if len(date_parts) == 3:
                date_val = date_parts[0][2:] + date_parts[1] + date_parts[2]
            else:
                date_val = d["date"]
            visit_val = re.sub(r'회$', '', d["visit"])
            for entry, val in [(self.date_entry, date_val),
                                (self.name_entry, d["name"]),
                                (self.visit_entry, visit_val)]:
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

        # 여러 항목 리뷰 모드: 항목마다 그룹 동적 계산
        if self.all_images:
            self._update_group_for_current(path)

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

        # 그룹 일괄 적용
        if self.apply_group_var.get() and len(self.group_paths) > 1:
            applied_paths = set()
            for gp in self.group_paths:
                if gp != path:
                    self.results[gp] = PhotoInfo(
                        date_raw=date_raw, year=2000 + yy, month=mm, day=dd,
                        patient_name=name, visit_number=visit_number,
                        confidence=1.0, source_path=gp, visit_raw=visit_raw,
                    )
                    applied_paths.add(gp)
            # 그룹 일괄 적용된 항목을 fail_items에서 제거
            if applied_paths:
                removed = 0
                new_fail_items = []
                for idx, item in enumerate(self.fail_items):
                    if item["path"] in applied_paths and idx > self.current_index:
                        removed += 1
                    else:
                        new_fail_items.append(item)
                self.fail_items = new_fail_items

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

    def _delete_current_file(self):
        if self.current_index >= len(self.fail_items):
            return
        item = self.fail_items[self.current_index]
        path = item["path"]
        filename = item["filename"]
        if not os.path.isfile(path):
            messagebox.showwarning("알림", f"파일을 찾을 수 없습니다:\n{filename}", parent=self.dialog)
            return
        if not messagebox.askyesno("파일 삭제",
                f"'{filename}' 파일을 휴지통으로 보내시겠습니까?", parent=self.dialog):
            return
        try:
            send2trash(path)
        except Exception as e:
            messagebox.showerror("오류", f"삭제 실패: {e}", parent=self.dialog)
            return
        self.results[path] = "deleted"
        self.fail_items.pop(self.current_index)
        if self.current_index >= len(self.fail_items):
            self.current_index = max(0, len(self.fail_items) - 1)
        if not self.fail_items:
            messagebox.showinfo("알림", "리뷰할 사진이 없습니다.", parent=self.dialog)
            self.dialog.destroy()
            return
        self._show_current()

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
        self.dialog.geometry("600x680")
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
                                  height=10, selectmode="extended")
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

        # 수기 추가 영역
        add_frame = tk.LabelFrame(main, text=" 수기 추가 ", font=("Segoe UI", 9),
                                   bg=COLORS["surface"], fg=COLORS["text_dim"],
                                   bd=1, relief="solid", padx=8, pady=8)
        add_frame.pack(fill=tk.X, pady=(8, 0))

        type_row = tk.Frame(add_frame, bg=COLORS["surface"])
        type_row.pack(fill=tk.X, pady=2)
        tk.Label(type_row, text="유형:", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
        self.add_type_var = tk.StringVar(value="이름")
        for txt in ("이름", "회차", "날짜"):
            tk.Radiobutton(type_row, text=txt, variable=self.add_type_var, value=txt,
                           bg=COLORS["surface"], fg=COLORS["text"], selectcolor=COLORS["surface2"],
                           activebackground=COLORS["surface"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))

        val_row = tk.Frame(add_frame, bg=COLORS["surface"])
        val_row.pack(fill=tk.X, pady=2)
        tk.Label(val_row, text="OCR 인식값:", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
        self.add_ocr_entry = tk.Entry(val_row, font=("Segoe UI", 10), bg=COLORS["surface2"],
                                       fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
                                       bd=0, highlightthickness=1, highlightcolor=COLORS["accent"],
                                       highlightbackground=COLORS["border"])
        self.add_ocr_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3, padx=(4, 0))

        corr_row = tk.Frame(add_frame, bg=COLORS["surface"])
        corr_row.pack(fill=tk.X, pady=2)
        tk.Label(corr_row, text="보정값:", bg=COLORS["surface"], fg=COLORS["text_dim"],
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
        self.add_corr_entry = tk.Entry(corr_row, font=("Segoe UI", 10), bg=COLORS["surface2"],
                                        fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
                                        bd=0, highlightthickness=1, highlightcolor=COLORS["accent"],
                                        highlightbackground=COLORS["border"])
        self.add_corr_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3, padx=(4, 0))

        _make_dark_button(add_frame, "추가", self._add_entry,
                          bg=COLORS["accent"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=12).pack(anchor=tk.E, pady=(6, 0))

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

    def _add_entry(self):
        type_map = {"이름": "name_corrections", "회차": "visit_corrections", "날짜": "date_corrections"}
        category = type_map[self.add_type_var.get()]
        ocr_val = self.add_ocr_entry.get().strip()
        corr_val = self.add_corr_entry.get().strip()
        if not ocr_val or not corr_val:
            messagebox.showwarning("입력 부족", "OCR 인식값과 보정값을 모두 입력해주세요.", parent=self.dialog)
            return
        self.data[category][ocr_val] = corr_val
        self._populate()
        self._save()
        self.add_ocr_entry.delete(0, tk.END)
        self.add_corr_entry.delete(0, tk.END)

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
# SettingsDialog: 설정 편집
# ============================================================

class SettingsDialog:
    def __init__(self, parent):
        import config as config_module
        import parser as parser_module
        self.config_mod = config_module
        self.parser_mod = parser_module

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("설정 편집")
        self.dialog.geometry("520x600")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()
        self.dialog.configure(bg=COLORS["bg"])
        self._build_ui()

    def _build_ui(self):
        main = tk.Frame(self.dialog, bg=COLORS["bg"], padx=15, pady=15)
        main.pack(fill=tk.BOTH, expand=True)

        tk.Label(main, text="설정 편집", font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor=tk.W, pady=(0, 5))
        tk.Label(main, text="변경 후 저장하면 즉시 적용됩니다. (재시작 불필요)",
                 font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["text_dim"]).pack(anchor=tk.W, pady=(0, 12))

        # 스크롤 가능한 설정 영역
        canvas = tk.Canvas(main, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(main, orient=tk.VERTICAL, command=canvas.yview)
        self.settings_frame = tk.Frame(canvas, bg=COLORS["bg"])
        self.settings_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.settings_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.entries = {}
        settings = [
            ("OCR 크롭 영역", None, None),
            ("CROP_TOP", "상단 시작 (0.0~1.0)", self.config_mod.CROP_TOP),
            ("CROP_BOTTOM", "하단 끝 (0.0~1.0)", self.config_mod.CROP_BOTTOM),
            ("CROP_LEFT", "좌측 시작 (0.0~1.0)", self.config_mod.CROP_LEFT),
            ("CROP_RIGHT", "우측 끝 (0.0~1.0)", self.config_mod.CROP_RIGHT),
            ("OCR 설정", None, None),
            ("CONFIDENCE_THRESHOLD", "OCR 신뢰도 임계값 (0.0~1.0)", self.config_mod.CONFIDENCE_THRESHOLD),
            ("IMAGE_MAX_SIZE", "이미지 최대 크기 (None=원본)", self.config_mod.IMAGE_MAX_SIZE),
            ("파싱 설정", None, None),
            ("VISIT_MIN", "회차 최소값", self.parser_mod.VISIT_MIN),
            ("VISIT_MAX", "회차 최대값", self.parser_mod.VISIT_MAX),
            ("DATE_TOLERANCE_DAYS", "날짜 보정 허용 오차 (일)", self.parser_mod.DATE_TOLERANCE_DAYS),
            ("그룹 설정", None, None),
            ("max_gap_seconds", "연속 촬영 그룹 간격 (초)", 60),
            ("폴더 형식", None, None),
            ("DATE_FOLDER_FORMAT", "날짜 폴더 형식", self.config_mod.DATE_FOLDER_FORMAT),
        ]

        for name, desc, value in settings:
            if value is None and desc is None:
                # 섹션 헤더
                tk.Label(self.settings_frame, text=name, font=("Segoe UI", 10, "bold"),
                         bg=COLORS["bg"], fg=COLORS["accent_light"]).pack(anchor=tk.W, pady=(12, 4))
                continue
            row = tk.Frame(self.settings_frame, bg=COLORS["bg"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=desc, width=30, anchor="w", bg=COLORS["bg"],
                     fg=COLORS["text_dim"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
            entry = tk.Entry(row, font=("Segoe UI", 10), bg=COLORS["surface2"],
                             fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
                             bd=0, highlightthickness=1, highlightcolor=COLORS["accent"],
                             highlightbackground=COLORS["border"])
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=3)
            entry.insert(0, str(value) if value is not None else "None")
            self.entries[name] = entry

        # 하단 버튼
        btn_frame = tk.Frame(main, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        _make_dark_button(btn_frame, "저장", self._save,
                          bg=COLORS["accent"], fg="white",
                          font=("Segoe UI", 9, "bold"), padx=16).pack(side=tk.LEFT, padx=(0, 8))
        _make_dark_button(btn_frame, "닫기", self.dialog.destroy, padx=12).pack(side=tk.RIGHT)

    def _parse_value(self, text):
        text = text.strip()
        if text.lower() == "none":
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        return text

    def _save(self):
        try:
            for name, entry in self.entries.items():
                val = self._parse_value(entry.get())
                if name in ("CROP_TOP", "CROP_BOTTOM", "CROP_LEFT", "CROP_RIGHT",
                            "CONFIDENCE_THRESHOLD", "IMAGE_MAX_SIZE", "DATE_FOLDER_FORMAT"):
                    setattr(self.config_mod, name, val)
                elif name in ("VISIT_MIN", "VISIT_MAX", "DATE_TOLERANCE_DAYS"):
                    setattr(self.parser_mod, name, val)
                elif name == "max_gap_seconds":
                    self.parser_mod._default_max_gap = val

            # config 모듈의 전역 변수도 갱신
            import config
            for attr in ("CROP_TOP", "CROP_BOTTOM", "CROP_LEFT", "CROP_RIGHT",
                         "CONFIDENCE_THRESHOLD", "IMAGE_MAX_SIZE", "DATE_FOLDER_FORMAT"):
                if attr in self.entries:
                    globals_val = getattr(self.config_mod, attr)
                    # ocr_engine에서 import한 값도 갱신
                    setattr(config, attr, globals_val)

            messagebox.showinfo("완료", "설정이 저장되었습니다.", parent=self.dialog)
        except Exception as e:
            messagebox.showerror("오류", f"저장 실패: {e}", parent=self.dialog)


# ============================================================
# OrganizerApp: 메인 GUI
# ============================================================

class OrganizerApp:
    def __init__(self):
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title("무좀 사진 자동 분류기")
        self.root.geometry("1000x800")
        self.root.minsize(800, 600)
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
        self.deleted_paths = set()
        self.cache_valid = False

        self._setup_style()
        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()

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

    def _build_menu(self):
        menubar = tk.Menu(self.root, bg=COLORS["surface"], fg=COLORS["text"],
                          activebackground=COLORS["accent"], activeforeground="white",
                          font=("Segoe UI", 9), bd=0)

        # 도구 메뉴
        tool_menu = tk.Menu(menubar, tearoff=0, bg=COLORS["surface"], fg=COLORS["text"],
                            activebackground=COLORS["accent"], activeforeground="white",
                            font=("Segoe UI", 9))
        tool_menu.add_command(label="설정 편집...", command=self._open_settings, accelerator="Ctrl+,")
        tool_menu.add_command(label="학습 보정 관리...", command=self._open_learned, accelerator="Ctrl+L")
        tool_menu.add_separator()
        tool_menu.add_command(label="입력 폴더 열기", command=lambda: _open_folder(self.input_dir.get()))
        tool_menu.add_command(label="출력 폴더 열기", command=lambda: _open_folder(self.output_dir.get()))
        tool_menu.add_command(label="리뷰 폴더 열기", command=lambda: _open_folder(self.review_dir.get()))
        tool_menu.add_separator()
        tool_menu.add_command(label="결과 전체 복사", command=self._copy_all_rows, accelerator="Ctrl+Shift+C")
        tool_menu.add_command(label="테이블 초기화", command=self._clear_table)
        menubar.add_cascade(label="도구", menu=tool_menu)

        self.root.config(menu=menubar)

    def _bind_shortcuts(self):
        self.root.bind("<F5>", lambda e: self._run_preview())
        self.root.bind("<F6>", lambda e: self._open_review())
        self.root.bind("<F7>", lambda e: self._run_organize())
        self.root.bind("<F8>", lambda e: self._toggle_pause())
        self.root.bind("<Escape>", lambda e: self._stop_processing())
        self.root.bind("<Control-l>", lambda e: self._open_learned())
        self.root.bind("<Control-L>", lambda e: self._open_learned())
        self.root.bind("<Control-comma>", lambda e: self._open_settings())
        self.root.bind("<Control-Shift-C>", lambda e: self._copy_all_rows())
        self.root.bind("<Delete>", lambda e: self._delete_selected_rows())

    def _open_settings(self):
        SettingsDialog(self.root)

    def _clear_table(self):
        self.tree.delete(*self.tree.get_children())

    def _delete_selected_rows(self):
        selected = self.tree.selection()
        if not selected:
            return
        for item in selected:
            fn = str(self.tree.item(item)["values"][0])
            self.cached_fail_items = [i for i in self.cached_fail_items if i["filename"] != fn]
            for path in list(self.cached_results.keys()):
                if os.path.basename(path) == fn:
                    del self.cached_results[path]
                    break
            self.tree.delete(item)

    def _build_ui(self):
        main = tk.Frame(self.root, bg=COLORS["bg"], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)

        # 타이틀 + 사용방법
        title_frame = tk.Frame(main, bg=COLORS["bg"])
        title_frame.pack(fill=tk.X, pady=(0, 12))
        title_left = tk.Frame(title_frame, bg=COLORS["bg"])
        title_left.pack(side=tk.LEFT)
        tk.Label(title_left, text="무좀 사진 자동 분류기", font=("Segoe UI", 18, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side=tk.LEFT)
        tk.Label(title_left, text="OCR Photo Organizer", font=("Segoe UI", 9),
                 bg=COLORS["bg"], fg=COLORS["text_muted"]).pack(side=tk.LEFT, padx=(10, 0), pady=(6, 0))
        help_text = (
            "1. 사진 우측상단에 날짜(YYMMDD) + 이름 + 회차 수기 기입\n"
            "2. 미리보기(F5)로 OCR 결과 확인\n"
            "3. 실패 항목 더블클릭하여 개별 리뷰/수정\n"
            "4. 분류 실행(F7)으로 폴더 자동 분류"
        )
        tk.Label(title_frame, text=help_text, font=("Segoe UI", 8),
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 justify=tk.LEFT).pack(side=tk.RIGHT, padx=(12, 0))

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
            main, text="사진 파일을 여기에 드래그 앤 드롭 (입력 폴더로 복사)",
            relief="flat", bg=COLORS["drop_bg"], fg=COLORS["text_dim"],
            font=("Segoe UI", 9), height=2, cursor="hand2",
            highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"])
        self.drop_frame.pack(fill=tk.X, pady=(0, 6))

        if HAS_DND:
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_frame.dnd_bind("<<DragEnter>>", lambda e: self.drop_frame.config(bg=COLORS["drop_hover"], highlightbackground=COLORS["accent"]))
            self.drop_frame.dnd_bind("<<DragLeave>>", lambda e: self.drop_frame.config(bg=COLORS["drop_bg"], highlightbackground=COLORS["border"]))
        else:
            self.drop_frame.config(text="드래그 앤 드롭 미지원 (찾아보기 버튼을 사용하세요)", fg=COLORS["text_muted"])

        # 파일 수
        tk.Label(main, textvariable=self.file_count_var, font=("Segoe UI", 9, "bold"),
                 bg=COLORS["bg"], fg=COLORS["accent_light"]).pack(anchor=tk.W, pady=(0, 4))
        self._update_file_count()

        # 버튼 행
        btn_frame = tk.Frame(main, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, pady=(0, 6))

        self.btn_preview = ttk.Button(btn_frame, text="미리보기 (F5)", command=self._run_preview)
        self.btn_preview.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_review = ttk.Button(btn_frame, text="실패 리뷰 (F6)", command=self._open_review, state="disabled")
        self.btn_review.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_run = ttk.Button(btn_frame, text="분류 실행 (F7)", command=self._run_organize, style="Accent.TButton")
        self.btn_run.pack(side=tk.LEFT, padx=(0, 6))

        # 구분선
        tk.Frame(btn_frame, bg=COLORS["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)

        self.btn_pause = ttk.Button(btn_frame, text="일시정지 (F8)", command=self._toggle_pause, state="disabled")
        self.btn_pause.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop = ttk.Button(btn_frame, text="정지 (Esc)", command=self._stop_processing, state="disabled")
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))

        # 옵션 행
        opt_frame = tk.Frame(main, bg=COLORS["bg"])
        opt_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(opt_frame, text="복사 모드 (원본 유지)", variable=self.copy_mode).pack(side=tk.LEFT)

        # 진행 바 + 상태
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 4))
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
        self.context_menu.add_command(label="파일 삭제", command=self._delete_selected_file)
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
        self._review_selected()

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

    def _delete_selected_file(self):
        selected = self.tree.selection()
        if not selected:
            return
        filename = str(self.tree.item(selected[0])["values"][0])
        # 파일 경로 찾기
        file_path = None
        for path in self.cached_results:
            if os.path.basename(path) == filename:
                file_path = path
                break
        if file_path is None:
            file_path = os.path.join(self.input_dir.get(), filename)
        if not os.path.isfile(file_path):
            messagebox.showwarning("알림", f"파일을 찾을 수 없습니다:\n{filename}")
            return
        if not messagebox.askyesno("파일 삭제", f"'{filename}' 파일을 휴지통으로 보내시겠습니까?"):
            return
        try:
            send2trash(file_path)
        except Exception as e:
            messagebox.showerror("오류", f"삭제 실패: {e}")
            return
        # 캐시에서 제거
        self.deleted_paths.add(file_path)
        self.cached_results.pop(file_path, None)
        self.cached_fail_items = [item for item in self.cached_fail_items if item["path"] != file_path]
        self.tree.delete(selected[0])
        self.btn_review.config(state="normal" if self.cached_fail_items else "disabled")

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
                    fail_item = {"path": path, "filename": filename,
                                 "reason": status if info is None else f"현재: {status}"}
                    if info is not None:
                        fail_item["existing_info"] = info
                    break
        if fail_item is None:
            input_path = os.path.join(self.input_dir.get(), filename)
            if os.path.isfile(input_path):
                fail_item = {"path": input_path, "filename": filename, "reason": status}
                # 트리에서 기존 파싱값 가져오기
                vals = self.tree.item(selected[0])["values"]
                date_str = str(vals[1])
                name_str = str(vals[2])
                visit_str = str(vals[3])
                if date_str != "-" and name_str != "-":
                    fail_item["existing_display"] = {"date": date_str, "name": name_str, "visit": visit_str}
        if fail_item is None:
            messagebox.showinfo("알림", f"'{filename}' 파일을 찾을 수 없습니다.")
            return

        # 연속 촬영 그룹 찾기 (항상 입력 폴더 기준으로 스캔)
        group_paths = []
        all_paths = self._scan_images(self.input_dir.get())
        target = fail_item["path"]
        # target이 all_paths에 없으면 파일명으로 매칭
        if target not in all_paths:
            target_fn = os.path.basename(target)
            for p in all_paths:
                if os.path.basename(p) == target_fn:
                    target = p
                    break
            else:
                all_paths.append(target)
        for group in group_consecutive_photos(all_paths):
            if target in group:
                group_paths = group
                break

        # 그룹 멤버의 파싱 결과 조회 (cached_results → 트리 테이블 폴백)
        group_cache = {}
        cache_by_name = {}
        if self.cached_results:
            cache_by_name = {os.path.basename(p): info for p, info in self.cached_results.items()}
        # 트리 테이블에서도 파싱 결과 수집
        tree_by_name = {}
        for tree_item in self.tree.get_children():
            vals = self.tree.item(tree_item)["values"]
            fn = str(vals[0])
            status = str(vals[4])
            if any(kw in status for kw in ("실패", "미분류", "오류", "텍스트 미발견")):
                tree_by_name[fn] = {"display": status, "failed": True}
            else:
                tree_by_name[fn] = {"date": str(vals[1]), "name": str(vals[2]), "visit": str(vals[3])}
        for gp in group_paths:
            fn = os.path.basename(gp)
            # cached_results에서 먼저 찾기 (None이 아닌 경우만)
            cached = self.cached_results.get(gp)
            if cached is None and fn in cache_by_name:
                cached = cache_by_name[fn]
            if cached is not None:
                group_cache[gp] = cached
            elif fn in tree_by_name:
                # 트리에서 요약 정보 제공 (성공/실패 모두)
                group_cache[gp] = tree_by_name[fn]
            else:
                group_cache[gp] = None

        dialog = ReviewDialog(self.root, [fail_item], group_paths=group_paths, group_cache=group_cache)
        self.root.wait_window(dialog.dialog)

        for path, info in dialog.get_results().items():
            if info == "deleted":
                self.deleted_paths.add(path)
                self.cached_results.pop(path, None)
                self.cached_fail_items = [item for item in self.cached_fail_items if item["path"] != path]
                fn = os.path.basename(path)
                for tree_item in self.tree.get_children():
                    if str(self.tree.item(tree_item)["values"][0]) == fn:
                        self.tree.delete(tree_item)
                        break
            elif info is not None:
                self.cached_results[path] = info
                self.cached_fail_items = [item for item in self.cached_fail_items if item["path"] != path]
                fn = os.path.basename(path)
                visit_str = info.visit_raw or f"{info.visit_number}회"
                for tree_item in self.tree.get_children():
                    if str(self.tree.item(tree_item)["values"][0]) == fn:
                        self.tree.item(tree_item, values=(
                            fn, f"{info.year}.{info.month:02d}.{info.day:02d}",
                            info.patient_name, visit_str, _make_status(info)))
                        break

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

    def _stop_processing(self):
        if not self.is_running:
            return
        self._abort_processing = True
        self._run_after_abort = False
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
        self._update_status_direct("분류 정지 중...")

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
            self.btn_stop.config(state="disabled")
            self.is_paused = False
            self.pause_event.set()
        else:
            self.btn_pause.config(state="normal")
            self.btn_stop.config(state="normal")
        self.btn_review.config(state="normal" if self.cached_fail_items else "disabled")

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
        all_images = self._scan_images(self.input_dir.get())
        # 트리 테이블의 파싱 결과를 cached_results에 보충 (미리보기 진행 중 대응)
        review_cache = dict(self.cached_results)
        tree_names = {}
        for tree_item in self.tree.get_children():
            vals = self.tree.item(tree_item)["values"]
            fn = str(vals[0])
            status = str(vals[4])
            if any(kw in status for kw in ("실패", "미분류", "오류", "텍스트 미발견")):
                tree_names[fn] = None
            else:
                tree_names[fn] = {"date": str(vals[1]), "name": str(vals[2]),
                                  "visit": str(vals[3]), "display": status}
        for img_path in all_images:
            fn = os.path.basename(img_path)
            if img_path not in review_cache and fn in tree_names:
                review_cache[img_path] = tree_names[fn]
        dialog = ReviewDialog(self.root, self.cached_fail_items,
                              all_images=all_images, cached_results=review_cache)
        self.root.wait_window(dialog.dialog)

        reviewed = 0
        deleted = 0
        for path, info in dialog.get_results().items():
            if info == "deleted":
                self.deleted_paths.add(path)
                self.cached_results.pop(path, None)
                deleted += 1
            elif info is not None:
                self.cached_results[path] = info
                reviewed += 1
        self.cached_fail_items = [item for item in self.cached_fail_items
                                   if dialog.get_results().get(item["path"]) is None]
        if not self.is_running:
            self._refresh_table()
        else:
            # 진행 중에는 개별 행만 업데이트
            for path, info in dialog.get_results().items():
                if info == "deleted":
                    fn = os.path.basename(path)
                    for tree_item in self.tree.get_children():
                        if str(self.tree.item(tree_item)["values"][0]) == fn:
                            self.tree.delete(tree_item)
                            break
                elif info is not None:
                    fn = os.path.basename(path)
                    visit_str = info.visit_raw or f"{info.visit_number}회"
                    for tree_item in self.tree.get_children():
                        if str(self.tree.item(tree_item)["values"][0]) == fn:
                            self.tree.item(tree_item, values=(
                                fn, f"{info.year}.{info.month:02d}.{info.day:02d}",
                                info.patient_name, visit_str, _make_status(info)))
                            break
        msg_parts = []
        if reviewed > 0:
            msg_parts.append(f"{reviewed}장 수기 입력됨")
        if deleted > 0:
            msg_parts.append(f"{deleted}장 삭제됨")
        if msg_parts and not self.is_running:
            self._update_status_direct(f"리뷰 완료! {', '.join(msg_parts)}  |  '분류 실행' 버튼을 누르면 모두 분류합니다.")
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
        self.deleted_paths = set()
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

        def _add_fail_item(item):
            if item["path"] in self.deleted_paths:
                return
            new_fail_items.append(item)
            if dry_run:
                self.cached_fail_items = [i for i in new_fail_items if i["path"] not in self.deleted_paths]
                if self.cached_fail_items:
                    self.root.after(0, lambda: self.btn_review.config(state="normal"))

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
                _add_fail_item({"path": image_path, "filename": filename, "reason": f"OCR 오류: {e}"})
                fail += 1
                self._update_progress(i + 1)
                continue

            if not lines:
                self._add_row(filename, "-", "-", "-", "텍스트 미발견")
                if not dry_run:
                    move_to_review(image_path, reason="텍스트 미발견", review_dir=review_dir)
                new_cache[image_path] = None
                _add_fail_item({"path": image_path, "filename": filename, "reason": "텍스트 미발견"})
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
                _add_fail_item({"path": image_path, "filename": filename, "reason": f"파싱 실패: {ocr_text}"})
                fail += 1
                self._update_progress(i + 1)
                continue

            vs = info.visit_raw or f"{info.visit_number}회"
            date_str = f"{info.year}.{info.month:02d}.{info.day:02d}"

            if dry_run:
                self._add_row(filename, date_str, info.patient_name, vs, _make_status(info))
                new_cache[image_path] = info
                if info.visit_review:
                    _add_fail_item({"path": image_path, "filename": filename,
                                    "reason": f"회차검토: {date_str} {info.patient_name} {vs}",
                                    "existing_info": info})
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
            # 삭제된 파일 제거
            for dp in self.deleted_paths:
                new_cache.pop(dp, None)
            new_fail_items = [item for item in new_fail_items if item["path"] not in self.deleted_paths]
            # 미리보기 중 리뷰에서 수정된 결과를 병합
            for path, info in self.cached_results.items():
                if info is not None and new_cache.get(path) is None and path not in self.deleted_paths:
                    new_cache[path] = info
                    new_fail_items = [item for item in new_fail_items if item["path"] != path]
                    success += 1
                    fail -= 1
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
