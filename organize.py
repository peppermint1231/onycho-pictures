"""무좀 사진 자동 분류 프로그램 - 메인 실행 파일"""
import os
import sys
import argparse

from config import INPUT_DIR, OUTPUT_DIR, SUPPORTED_EXTENSIONS
from ocr_engine import get_text_lines
from parser import parse_photo
from file_manager import move_photo, move_to_review, get_target_folder


def scan_images(input_dir):
    """입력 폴더에서 이미지 파일 목록을 가져옵니다."""
    images = []
    for filename in os.listdir(input_dir):
        ext = os.path.splitext(filename)[1].lower()
        if ext in SUPPORTED_EXTENSIONS:
            images.append(os.path.join(input_dir, filename))
    images.sort()
    return images


def process_images(input_dir=None, output_dir=None, copy=False, dry_run=False):
    """메인 처리 로직"""
    if input_dir is None:
        input_dir = INPUT_DIR
    if output_dir is None:
        output_dir = OUTPUT_DIR

    # 입력 폴더 확인
    if not os.path.exists(input_dir):
        print(f"입력 폴더가 없습니다: {input_dir}")
        print(f"폴더를 생성합니다...")
        os.makedirs(input_dir, exist_ok=True)
        print(f"'{input_dir}' 폴더에 사진을 넣고 다시 실행해주세요.")
        return

    # 이미지 스캔
    images = scan_images(input_dir)
    if not images:
        print(f"'{input_dir}' 폴더에 이미지가 없습니다.")
        print(f"지원 형식: {', '.join(SUPPORTED_EXTENSIONS)}")
        return

    print(f"\n총 {len(images)}장의 사진을 발견했습니다.")
    print("=" * 50)

    success_count = 0
    fail_count = 0
    results = []

    for i, image_path in enumerate(images, 1):
        filename = os.path.basename(image_path)
        print(f"\n[{i}/{len(images)}] {filename}")

        # OCR 텍스트 추출
        try:
            lines, confidence = get_text_lines(image_path)
        except Exception as e:
            print(f"  OCR 오류: {e}")
            if not dry_run:
                move_to_review(image_path, reason=f"OCR 오류: {e}")
            fail_count += 1
            continue

        if not lines:
            print(f"  텍스트를 찾지 못했습니다.")
            if not dry_run:
                move_to_review(image_path, reason="텍스트 미발견")
            fail_count += 1
            continue

        print(f"  OCR 결과: {lines}")
        print(f"  신뢰도: {confidence:.2f}")

        # 파싱
        info = parse_photo(lines, confidence, image_path)

        if info is None:
            print(f"  날짜/이름/회차를 파싱할 수 없습니다.")
            if not dry_run:
                move_to_review(image_path, reason=f"파싱 실패 - OCR: {lines}")
            fail_count += 1
            continue

        target_folder = get_target_folder(info, output_dir)
        print(f"  환자: {info.patient_name}")
        print(f"  날짜: {info.year}년 {info.month:02d}월 {info.day:02d}일")
        print(f"  회차: {info.visit_number}회")
        print(f"  대상 폴더: {target_folder}")

        if dry_run:
            print(f"  [DRY RUN] 이동하지 않음")
        else:
            action = "복사" if copy else "이동"
            dest = move_photo(info, output_dir, copy)
            print(f"  {action} 완료: {dest}")

        success_count += 1
        results.append(info)

    # 결과 요약
    print("\n" + "=" * 50)
    print(f"처리 완료!")
    print(f"  성공: {success_count}장")
    print(f"  실패 (수동 확인 필요): {fail_count}장")

    if fail_count > 0 and not dry_run:
        print(f"  → '_review' 폴더에서 실패한 사진을 확인해주세요.")

    if dry_run:
        print(f"\n[DRY RUN 모드] 실제 파일 이동은 수행되지 않았습니다.")
        print(f"실제 실행하려면 --dry-run 옵션을 제거하세요.")


def main():
    parser = argparse.ArgumentParser(
        description="무좀 사진 자동 분류 프로그램",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python organize.py                    기본 실행 (input → output)
  python organize.py --dry-run          미리보기 (파일 이동 안 함)
  python organize.py --copy             이동 대신 복사
  python organize.py --input ./photos   입력 폴더 지정
        """,
    )
    parser.add_argument("--input", "-i", default=None, help="입력 폴더 경로")
    parser.add_argument("--output", "-o", default=None, help="출력 폴더 경로")
    parser.add_argument("--copy", "-c", action="store_true", help="이동 대신 복사")
    parser.add_argument("--dry-run", "-d", action="store_true", help="미리보기 모드")

    args = parser.parse_args()
    process_images(
        input_dir=args.input,
        output_dir=args.output,
        copy=args.copy,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
