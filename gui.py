"""무좀 사진 자동 분류 - GUI (tkinter + 드래그앤드롭 + 리뷰)"""
import os
import sys
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
from parser import parse_photo, PhotoInfo
from file_manager import move_photo, move_to_review, get_target_folder


class ReviewDialog:
    """실패한 사진의 OCR 영역을 보여주고 수기 입력을 받는 다이얼로그"""

    def __init__(self, parent, fail_items):
        """
        fail_items: list of dict
            {"path": str, "filename": str, "reason": str}
        """
        self.parent = parent
        self.fail_items = fail_items
        self.results = {}  # {path: PhotoInfo or None}
        self.current_index = 0

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("수동 분류 - 실패 사진 리뷰")
        self.dialog.geometry("700x550")
        self.dialog.resizable(True, True)
        self.dialog.grab_set()

        self._build_ui()
        self._show_current()

    def _build_ui(self):
        main = ttk.Frame(self.dialog, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # 상단: 진행 상황
        top = ttk.Frame(main)
        top.pack(fill=tk.X, pady=(0, 10))

        self.progress_label = ttk.Label(top, text="", font=("맑은 고딕", 10, "bold"))
        self.progress_label.pack(side=tk.LEFT)

        # 중단: 이미지 + 입력
        mid = ttk.Frame(main)
        mid.pack(fill=tk.BOTH, expand=True)

        # 왼쪽: 크롭된 이미지 미리보기
        img_frame = ttk.LabelFrame(mid, text="OCR 스캔 영역", padding=5)
        img_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.img_label = tk.Label(img_frame, bg="#333333")
        self.img_label.pack(fill=tk.BOTH, expand=True)

        # 오른쪽: 입력 필드
        input_frame = ttk.LabelFrame(mid, text="수동 입력", padding=10)
        input_frame.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(input_frame, text="파일명:").pack(anchor=tk.W, pady=(0, 2))
        self.filename_var = tk.StringVar()
        ttk.Label(input_frame, textvariable=self.filename_var, font=("맑은 고딕", 9), wraplength=200).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(input_frame, text="실패 사유:").pack(anchor=tk.W, pady=(0, 2))
        self.reason_var = tk.StringVar()
        ttk.Label(input_frame, textvariable=self.reason_var, font=("맑은 고딕", 8), foreground="red", wraplength=200).pack(anchor=tk.W, pady=(0, 15))

        ttk.Label(input_frame, text="날짜 (YYMMDD):").pack(anchor=tk.W, pady=(0, 2))
        self.date_entry = ttk.Entry(input_frame, width=20, font=("맑은 고딕", 12))
        self.date_entry.pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(input_frame, text="환자 이름:").pack(anchor=tk.W, pady=(0, 2))
        self.name_entry = ttk.Entry(input_frame, width=20, font=("맑은 고딕", 12))
        self.name_entry.pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(input_frame, text="회차:").pack(anchor=tk.W, pady=(0, 2))
        self.visit_entry = ttk.Entry(input_frame, width=20, font=("맑은 고딕", 12))
        self.visit_entry.pack(anchor=tk.W, pady=(0, 15))

        # 버튼들
        btn_frame = ttk.Frame(input_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="저장 & 다음", command=self._save_and_next).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="건너뛰기", command=self._skip).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="모두 건너뛰기", command=self._skip_all).pack(fill=tk.X, pady=2)

        # 하단: 네비게이션
        nav = ttk.Frame(main)
        nav.pack(fill=tk.X, pady=(10, 0))

        self.btn_prev = ttk.Button(nav, text="이전", command=self._prev)
        self.btn_prev.pack(side=tk.LEFT)

        self.btn_done = ttk.Button(nav, text="리뷰 완료", command=self._done)
        self.btn_done.pack(side=tk.RIGHT)

        # Enter 키로 저장
        self.dialog.bind("<Return>", lambda e: self._save_and_next())

    def _get_cropped_image(self, image_path):
        """OCR 크롭 영역의 이미지를 가져옵니다."""
        img = Image.open(image_path)
        w, h = img.size

        if CROP_TOP is not None:
            left = int(w * CROP_LEFT)
            right = int(w * CROP_RIGHT)
            top = int(h * CROP_TOP)
            bottom = int(h * CROP_BOTTOM)
            img = img.crop((left, top, right, bottom))

        return img

    def _show_current(self):
        if self.current_index >= len(self.fail_items):
            self._done()
            return

        item = self.fail_items[self.current_index]
        path = item["path"]

        self.progress_label.config(
            text=f"실패 사진 리뷰: {self.current_index + 1} / {len(self.fail_items)}"
        )
        self.filename_var.set(item["filename"])
        self.reason_var.set(item["reason"])

        # 이전에 입력한 값이 있으면 복원
        if path in self.results and self.results[path] is not None:
            info = self.results[path]
            self.date_entry.delete(0, tk.END)
            self.date_entry.insert(0, info.date_raw)
            self.name_entry.delete(0, tk.END)
            self.name_entry.insert(0, info.patient_name)
            self.visit_entry.delete(0, tk.END)
            self.visit_entry.insert(0, str(info.visit_number))
        else:
            self.date_entry.delete(0, tk.END)
            self.name_entry.delete(0, tk.END)
            self.visit_entry.delete(0, tk.END)

        # 크롭 이미지 표시
        try:
            crop_img = self._get_cropped_image(path)
            # 미리보기 크기로 리사이즈
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
            self.img_label.config(image="", text=f"이미지 로드 실패:\n{e}")

        # 날짜 입력에 포커스
        self.date_entry.focus_set()

        # 이전 버튼 상태
        self.btn_prev.config(state="normal" if self.current_index > 0 else "disabled")

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

        if not visit.isdigit():
            messagebox.showwarning("형식 오류", "회차는 숫자로 입력해주세요.", parent=self.dialog)
            return

        yy = int(date_raw[0:2])
        mm = int(date_raw[2:4])
        dd = int(date_raw[4:6])

        if mm < 1 or mm > 12 or dd < 1 or dd > 31:
            messagebox.showwarning("형식 오류", "유효하지 않은 날짜입니다.", parent=self.dialog)
            return

        path = self.fail_items[self.current_index]["path"]
        info = PhotoInfo(
            date_raw=date_raw,
            year=2000 + yy,
            month=mm,
            day=dd,
            patient_name=name,
            visit_number=int(visit),
            confidence=1.0,
            source_path=path,
        )
        self.results[path] = info

        self.current_index += 1
        self._show_current()

    def _skip(self):
        path = self.fail_items[self.current_index]["path"]
        self.results[path] = None
        self.current_index += 1
        self._show_current()

    def _skip_all(self):
        for i in range(self.current_index, len(self.fail_items)):
            path = self.fail_items[i]["path"]
            if path not in self.results:
                self.results[path] = None
        self._done()

    def _prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._show_current()

    def _done(self):
        # 아직 처리 안 된 항목은 None으로
        for item in self.fail_items:
            if item["path"] not in self.results:
                self.results[item["path"]] = None
        self.dialog.destroy()

    def get_results(self):
        """리뷰 결과 반환: {path: PhotoInfo or None}"""
        return self.results


class OrganizerApp:
    def __init__(self):
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("무좀 사진 자동 분류기")
        self.root.geometry("800x700")
        self.root.resizable(True, True)

        # 변수
        self.input_dir = tk.StringVar(value=INPUT_DIR)
        self.output_dir = tk.StringVar(value=OUTPUT_DIR)
        self.review_dir = tk.StringVar(value=REVIEW_DIR)
        self.copy_mode = tk.BooleanVar(value=False)
        self.is_running = False

        # 미리보기 결과 캐시
        self.cached_results = {}  # {path: PhotoInfo or None}
        self.cached_fail_items = []  # [{"path":..., "filename":..., "reason":...}]
        self.cache_valid = False

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(main, text="무좀 사진 자동 분류기", font=("맑은 고딕", 16, "bold"))
        title.pack(pady=(0, 15))

        # --- 폴더 설정 ---
        folder_frame = ttk.LabelFrame(main, text="폴더 설정", padding=10)
        folder_frame.pack(fill=tk.X, pady=(0, 10))

        for label_text, var, browse_cmd in [
            ("입력 폴더:", self.input_dir, self._browse_input),
            ("출력 폴더:", self.output_dir, self._browse_output),
            ("리뷰 폴더:", self.review_dir, self._browse_review),
        ]:
            row = ttk.Frame(folder_frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=label_text, width=12).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            ttk.Button(row, text="찾아보기", command=browse_cmd).pack(side=tk.RIGHT)

        # --- 드래그 앤 드롭 ---
        self.drop_frame = tk.Label(
            main,
            text="여기에 폴더를 드래그 앤 드롭하세요\n(입력 폴더로 설정됩니다)",
            relief="groove", bg="#f0f0f0", font=("맑은 고딕", 10), height=3,
        )
        self.drop_frame.pack(fill=tk.X, pady=(0, 10))

        if HAS_DND:
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_frame.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.drop_frame.dnd_bind("<<DragLeave>>", self._on_drag_leave)
        else:
            self.drop_frame.config(text="드래그 앤 드롭 미지원\n(찾아보기 버튼을 사용하세요)", fg="gray")

        # --- 옵션 ---
        option_frame = ttk.Frame(main)
        option_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(option_frame, text="복사 모드 (원본 유지)", variable=self.copy_mode).pack(side=tk.LEFT)

        # --- 버튼 ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        self.btn_preview = ttk.Button(btn_frame, text="미리보기", command=self._run_preview)
        self.btn_preview.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_review = ttk.Button(btn_frame, text="실패 사진 리뷰", command=self._open_review, state="disabled")
        self.btn_review.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_run = ttk.Button(btn_frame, text="분류 실행", command=self._run_organize)
        self.btn_run.pack(side=tk.LEFT)

        # 진행 바
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))

        # 상태
        self.status_var = tk.StringVar(value="준비 완료")
        ttk.Label(main, textvariable=self.status_var, font=("맑은 고딕", 9)).pack(anchor=tk.W)

        # --- 결과 테이블 ---
        table_frame = ttk.LabelFrame(main, text="처리 결과", padding=5)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        columns = ("파일명", "날짜", "환자명", "회차", "상태")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col, w in [("파일명", 200), ("날짜", 120), ("환자명", 100), ("회차", 60), ("상태", 150)]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 우클릭 메뉴
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="선택 행 복사", command=self._copy_selected_row)
        self.context_menu.add_command(label="전체 결과 복사", command=self._copy_all_rows)
        self.tree.bind("<Button-3>", self._show_context_menu)

    # --- 드래그 앤 드롭 ---
    def _on_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        self.input_dir.set(folder)
        self.cache_valid = False
        self.drop_frame.config(bg="#f0f0f0", text=f"입력 폴더 설정됨:\n{folder}")

    def _on_drag_enter(self, event):
        self.drop_frame.config(bg="#d0e8ff")

    def _on_drag_leave(self, event):
        self.drop_frame.config(bg="#f0f0f0")

    # --- 우클릭 ---
    def _show_context_menu(self, event):
        self.context_menu.post(event.x_root, event.y_root)

    def _copy_selected_row(self):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0])["values"]
        self.root.clipboard_clear()
        self.root.clipboard_append("\t".join(str(v) for v in values))

    def _copy_all_rows(self):
        rows = ["\t".join(["파일명", "날짜", "환자명", "회차", "상태"])]
        for item in self.tree.get_children():
            values = self.tree.item(item)["values"]
            rows.append("\t".join(str(v) for v in values))
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(rows))

    # --- 폴더 찾아보기 ---
    def _browse_input(self):
        path = filedialog.askdirectory(title="입력 폴더 선택")
        if path:
            self.input_dir.set(path)
            self.cache_valid = False

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
        # 리뷰 버튼은 실패 항목이 있을 때만 활성화
        if enabled and self.cached_fail_items:
            self.btn_review.config(state="normal")
        else:
            self.btn_review.config(state="disabled" if not enabled else "disabled")

    def _scan_images(self, input_dir):
        if not os.path.exists(input_dir):
            return []
        images = []
        for f in os.listdir(input_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                images.append(os.path.join(input_dir, f))
        images.sort()
        return images

    # --- 리뷰 다이얼로그 ---
    def _open_review(self):
        if not self.cached_fail_items:
            messagebox.showinfo("알림", "리뷰할 실패 사진이 없습니다.")
            return

        dialog = ReviewDialog(self.root, self.cached_fail_items)
        self.root.wait_window(dialog.dialog)

        results = dialog.get_results()

        # 캐시 업데이트: 수기 입력된 항목 반영
        reviewed_count = 0
        for path, info in results.items():
            if info is not None:
                self.cached_results[path] = info
                reviewed_count += 1

        # 실패 목록에서 수기 입력 완료된 항목 제거
        self.cached_fail_items = [
            item for item in self.cached_fail_items
            if results.get(item["path"]) is None
        ]

        # 테이블 갱신
        self._refresh_table()

        if reviewed_count > 0:
            self._update_status_direct(
                f"리뷰 완료! {reviewed_count}장 수기 입력됨  |  "
                f"'분류 실행' 버튼을 누르면 모두 분류합니다."
            )

        # 리뷰 버튼 상태 갱신
        if self.cached_fail_items:
            self.btn_review.config(state="normal")
        else:
            self.btn_review.config(state="disabled")

    def _refresh_table(self):
        """캐시 기반으로 테이블을 다시 그립니다."""
        self.tree.delete(*self.tree.get_children())

        for path, info in self.cached_results.items():
            filename = os.path.basename(path)
            if info is not None:
                date_str = f"{info.year}.{info.month:02d}.{info.day:02d}"
                visit_str = f"{info.visit_number}회"
                status = f"→ {info.date_raw} {info.patient_name} {visit_str}"
                self.tree.insert("", tk.END, values=(filename, date_str, info.patient_name, visit_str, status))
            else:
                self.tree.insert("", tk.END, values=(filename, "-", "-", "-", "미분류 (리뷰 필요)"))

    def _update_status_direct(self, text):
        self.status_var.set(text)

    # --- 미리보기 ---
    def _run_preview(self):
        self.cache_valid = False
        self.cached_fail_items = []
        self._start_processing(dry_run=True)

    # --- 분류 실행 ---
    def _run_organize(self):
        if self.cache_valid and self.cached_results:
            success_count = sum(1 for v in self.cached_results.values() if v is not None)
            fail_count = sum(1 for v in self.cached_results.values() if v is None)
            total = len(self.cached_results)

            msg = f"{success_count}장을 분류하시겠습니까?"
            if fail_count > 0:
                msg += f"\n(미분류 {fail_count}장은 리뷰 폴더로 이동)"
            msg += f"\n\n모드: {'복사' if self.copy_mode.get() else '이동'}"
            msg += f"\n출력: {self.output_dir.get()}"

            if messagebox.askyesno("확인", msg):
                self._start_processing(dry_run=False, use_cache=True)
            return

        input_dir = self.input_dir.get()
        images = self._scan_images(input_dir)
        if not images:
            messagebox.showwarning("알림", "입력 폴더에 이미지가 없습니다.")
            return

        if messagebox.askyesno(
            "확인",
            f"{len(images)}장의 사진을 분류하시겠습니까?\n\n"
            f"모드: {'복사' if self.copy_mode.get() else '이동'}\n"
            f"입력: {input_dir}\n출력: {self.output_dir.get()}",
        ):
            self._start_processing(dry_run=False)

    def _start_processing(self, dry_run, use_cache=False):
        if self.is_running:
            return
        self.is_running = True
        self._set_buttons_enabled(False)
        self.tree.delete(*self.tree.get_children())

        thread = threading.Thread(target=self._process, args=(dry_run, use_cache), daemon=True)
        thread.start()

    def _process(self, dry_run, use_cache=False):
        output_dir = self.output_dir.get()
        review_dir = self.review_dir.get()
        copy = self.copy_mode.get()

        # === 캐시 사용 ===
        if use_cache and self.cached_results:
            items = list(self.cached_results.items())
            total = len(items)
            self.root.after(0, lambda: self.progress.config(maximum=total, value=0))
            self._update_status(f"[실행] {total}장 분류 중...")

            success = 0
            fail = 0

            for i, (image_path, info) in enumerate(items):
                filename = os.path.basename(image_path)

                if info is None:
                    self._add_row(filename, "-", "-", "-", "미분류 → 리뷰 폴더")
                    move_to_review(image_path, reason="미분류", review_dir=review_dir)
                    fail += 1
                    self._update_progress(i + 1)
                    continue

                date_str = f"{info.year}.{info.month:02d}.{info.day:02d}"
                visit_str = f"{info.visit_number}회"
                action = "복사" if copy else "이동"

                try:
                    dest = move_photo(info, output_dir, copy)
                    self._add_row(filename, date_str, info.patient_name, visit_str, f"{action} 완료")
                    success += 1
                except Exception as e:
                    self._add_row(filename, date_str, info.patient_name, visit_str, f"실패: {e}")
                    fail += 1

                self._update_progress(i + 1)

            self._update_status(f"분류 완료! 성공: {success}장 / 실패: {fail}장")
            self.cache_valid = False
            self.cached_results.clear()
            self.cached_fail_items.clear()

            if fail > 0:
                self.root.after(0, lambda: messagebox.showinfo(
                    "완료", f"성공: {success}장\n실패: {fail}장\n\n리뷰 폴더를 확인해주세요."))
            else:
                self.root.after(0, lambda: messagebox.showinfo(
                    "완료", f"모든 사진({success}장)이 성공적으로 분류되었습니다!"))

            self._finish()
            return

        # === 일반 OCR 처리 ===
        input_dir = self.input_dir.get()
        images = self._scan_images(input_dir)
        total = len(images)

        if total == 0:
            self._update_status("입력 폴더에 이미지가 없습니다.")
            self._finish()
            return

        self.root.after(0, lambda: self.progress.config(maximum=total, value=0))
        mode_text = "[미리보기]" if dry_run else "[실행]"
        self._update_status(f"{mode_text} {total}장 처리 중...")

        success = 0
        fail = 0
        new_cache = {}
        new_fail_items = []

        for i, image_path in enumerate(images):
            filename = os.path.basename(image_path)
            self._update_status(f"{mode_text} ({i+1}/{total}) {filename}")

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

            date_str = f"{info.year}.{info.month:02d}.{info.day:02d}"
            visit_str = f"{info.visit_number}회"

            if dry_run:
                status = f"→ {info.date_raw} {info.patient_name} {visit_str}"
                new_cache[image_path] = info
            else:
                action = "복사" if copy else "이동"
                try:
                    dest = move_photo(info, output_dir, copy)
                    status = f"{action} 완료"
                except Exception as e:
                    status = f"실패: {e}"
                    fail += 1
                    self._update_progress(i + 1)
                    continue

            self._add_row(filename, date_str, info.patient_name, visit_str, status)
            success += 1
            self._update_progress(i + 1)

        if dry_run:
            self.cached_results = new_cache
            self.cached_fail_items = new_fail_items
            self.cache_valid = True

        mode = "미리보기" if dry_run else "분류"

        if dry_run:
            msg = f"미리보기 완료! 성공: {success}장 / 실패: {fail}장"
            if fail > 0:
                msg += f"  |  '실패 사진 리뷰' 버튼으로 수기 입력 가능"
            else:
                msg += f"  |  '분류 실행' 버튼을 누르면 바로 분류합니다."
            self._update_status(msg)
        else:
            self._update_status(f"{mode} 완료! 성공: {success}장 / 실패: {fail}장")
            if fail > 0:
                self.root.after(0, lambda: messagebox.showinfo(
                    "완료", f"성공: {success}장\n실패: {fail}장\n\n리뷰 폴더를 확인해주세요."))
            else:
                self.root.after(0, lambda: messagebox.showinfo(
                    "완료", f"모든 사진({success}장)이 성공적으로 분류되었습니다!"))

        self._finish()

    def _update_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _update_progress(self, value):
        self.root.after(0, lambda: self.progress.config(value=value))

    def _add_row(self, filename, date, name, visit, status):
        self.root.after(0, lambda: self.tree.insert("", tk.END, values=(
            filename, date, name, visit, status
        )))

    def _finish(self):
        self.is_running = False
        self.root.after(0, lambda: self._set_buttons_enabled(True))

    def run(self):
        self.root.mainloop()


def main():
    app = OrganizerApp()
    app.run()


if __name__ == "__main__":
    main()
