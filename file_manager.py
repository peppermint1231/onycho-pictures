"""폴더 생성 및 파일 이동/복사 관리"""
import os
import shutil
from config import OUTPUT_DIR, REVIEW_DIR, DATE_FOLDER_FORMAT, COPY_MODE
from parser import PhotoInfo


def get_target_folder(info, output_dir=None):
    """
    PhotoInfo 기반으로 대상 폴더 경로를 생성합니다.

    예: output/2026년 03월 31일/260331 김익명 3회/
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    date_folder = DATE_FOLDER_FORMAT.format(
        year=info.year, month=info.month, day=info.day
    )
    visit_str = info.visit_raw if info.visit_raw else f"{info.visit_number}회"
    patient_folder = f"{info.date_raw} {info.patient_name} {visit_str}"

    return os.path.join(output_dir, date_folder, patient_folder)


def move_photo(info, output_dir=None, copy=None):
    """
    사진을 대상 폴더로 이동 또는 복사합니다.

    Returns:
        str: 이동된 파일의 최종 경로
    """
    if copy is None:
        copy = COPY_MODE

    target_folder = get_target_folder(info, output_dir)
    os.makedirs(target_folder, exist_ok=True)

    filename = os.path.basename(info.source_path)
    target_path = os.path.join(target_folder, filename)

    # 동일 파일명 존재 시 번호 추가
    if os.path.exists(target_path):
        name, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(target_path):
            target_path = os.path.join(target_folder, f"{name}_{counter}{ext}")
            counter += 1

    if copy:
        shutil.copy2(info.source_path, target_path)
    else:
        shutil.move(info.source_path, target_path)

    return target_path


def move_to_review(source_path, reason="", review_dir=None):
    """
    OCR 실패 등으로 분류 불가한 사진을 리뷰 폴더로 이동합니다.
    """
    if review_dir is None:
        review_dir = REVIEW_DIR
    os.makedirs(review_dir, exist_ok=True)

    filename = os.path.basename(source_path)
    target_path = os.path.join(review_dir, filename)

    # 동일 파일명 존재 시 번호 추가
    if os.path.exists(target_path):
        name, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(target_path):
            target_path = os.path.join(review_dir, f"{name}_{counter}{ext}")
            counter += 1

    shutil.move(source_path, target_path)

    # 리뷰 로그에 기록
    log_path = os.path.join(review_dir, "review_log.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{filename} | 사유: {reason}\n")

    return target_path
