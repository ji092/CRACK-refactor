"""제보 파일 처리 코어 로직.

업로드 파일 저장(안전한 파일명 생성 포함), EXIF GPS 추출, EXIF 파기/HEIC 변환을 담당한다.
Flask의 request/session에 의존하지 않는다.
(기존 report_service.py의 upload_file/submit_report에 중복돼 있던 로직을 통합)
"""
import math
import os
import time
import uuid

from werkzeug.utils import secure_filename

from utils import allowed_file

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'heif'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'm4v'}


def make_safe_filename(original_name):
    """원본 파일명에서 타임스탬프가 붙은 안전한 파일명을 생성한다.

    secure_filename()은 한글 문자를 전부 제거하여 빈 문자열을 만들 수 있으므로
    (예: "안성스타필드.heic" → ""), 확장자를 먼저 보존한 뒤 UUID로 폴백한다.
    """
    original_ext = ''
    if '.' in original_name:
        original_ext = original_name.rsplit('.', 1)[1].lower()

    safe_name = secure_filename(original_name)
    if not safe_name or '.' not in safe_name:
        safe_name = f"{uuid.uuid4().hex[:12]}.{original_ext}" if original_ext else safe_name

    return f"{int(time.time())}_{safe_name}", original_ext


def save_upload(file, base_dir=None):
    """업로드 파일을 종류(이미지/영상)에 맞는 디렉토리에 저장한다.

    반환: {'ok': bool, 'file_type': 'image'|'video'|None,
           'save_path': 절대경로, 'web_path': '/uploads/...', 'filename': str, 'ext': str}
    """
    if base_dir is None:
        base_dir = os.getcwd()
    filename, original_ext = make_safe_filename(file.filename)

    if allowed_file(filename, ALLOWED_IMAGE_EXTENSIONS):
        sub_dir, file_type = 'images', 'image'
    elif allowed_file(filename, ALLOWED_VIDEO_EXTENSIONS):
        sub_dir, file_type = 'videos', 'video'
    else:
        return {'ok': False, 'file_type': None, 'save_path': None, 'web_path': None,
                'filename': filename, 'ext': original_ext}

    save_path = os.path.join(base_dir, 'uploads', sub_dir, filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    file.save(save_path)

    return {'ok': True, 'file_type': file_type, 'save_path': save_path,
            'web_path': f'/uploads/{sub_dir}/{filename}', 'filename': filename, 'ext': original_ext}


def sanitize_coord(value):
    """위도/경도 값에서 NaN/inf를 걸러내고 float 또는 None을 반환한다."""
    if value is None or value == '':
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def strip_exif_and_convert(save_path, filename, base_dir=None):
    """이미지의 EXIF 메타데이터를 파기하고 재저장한다 (개인정보 보호).

    HEIC/HEIF는 JPG로 변환한다. 반환: (최종 save_path, 최종 web_path)
    """
    if base_dir is None:
        base_dir = os.getcwd()
    try:
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()

        image = Image.open(save_path)
        file_ext = filename.rsplit('.', 1)[1].lower()

        if file_ext in ['heic', 'heif']:
            new_filename = filename.rsplit('.', 1)[0] + ".jpg"
            new_save_path = os.path.join(base_dir, 'uploads', 'images', new_filename)
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(new_save_path, "JPEG", quality=85)
            os.remove(save_path)
            return new_save_path, f'/uploads/images/{new_filename}'
        else:
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(save_path, "JPEG", quality=85)
            return save_path, f'/uploads/images/{filename}'
    except Exception as e:
        print(f"Image processing (EXIF Strip) Error: {e}")
        return save_path, f'/uploads/images/{filename}'
