"""AI 분석 코어 로직.

YOLO 추론 → DB 상태 갱신 → 포인트 적립 → 썸네일/재인코딩 영상 생성을 담당한다.
Flask의 request/session에 의존하지 않으며, app 객체는 DB 컨텍스트 확보용으로만 주입받는다.
(기존 app.py의 run_ai_analysis를 그대로 이동)
"""
import os

import cv2

from extensions import db
from models import Report, AiResult, Member, PointLog, VideoDetection


def run_ai_analysis(app, model, base_dir, report_id, file_path, file_type):
    if not model:
        return
    abs_path = os.path.join(base_dir, file_path.lstrip('/'))
    try:
        is_damaged = False
        max_conf = 0.0
        pothole_max_conf = 0.0
        max_pothole_in_frame = 0
        total_pothole_count = 0
        sinkhole_count = 0
        damage_type = "없음"
        annotated_path = None
        encoded_video_path = None

        if file_type == 'video':
            # === 동영상 분석: 프레임 추출 후 YOLO 분석 및 박스 오버레이 인코딩 ===
            print(f"[AI Video] Starting video analysis: {abs_path}")
            cap = cv2.VideoCapture(abs_path)
            if not cap.isOpened():
                print(f"[AI Video] ERROR: Cannot open video file")
                return

            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            # 출력 파일 설정 (H.264 코덱 사용)
            name, ext = os.path.splitext(os.path.basename(abs_path))
            output_filename = f"res_{name}.mp4"
            output_abs_path = os.path.join(os.path.dirname(abs_path), output_filename)
            fourcc = cv2.VideoWriter_fourcc(*'avc1')  # 웹 표준 H.264 (Chrome/Safari 필수)
            out = cv2.VideoWriter(output_abs_path, fourcc, fps, (width, height))
            if not out.isOpened():
                # avc1 코덱을 사용할 수 없는 환경 대비 mp4v로 폴백
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(output_abs_path, fourcc, fps, (width, height))

            best_frame = None
            best_result = None
            best_conf = 0.0
            frame_idx = 0
            frame_detections = []

            sample_interval = max(int(fps // 5), 1)

            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_h, frame_w = frame.shape[:2]
                current_time_sec = frame_idx / fps

                results = model(frame, verbose=False)
                # 현재 프레임에 CV 박스 그리기
                annotated_frame = results[0].plot()
                out.write(annotated_frame)

                # DB 저장용 데이터 추출 (초당 약 5번만 기록)
                if frame_idx % sample_interval == 0:
                    for r in results:
                        if len(r.boxes) > 0:
                            frame_pothole_count = 0
                            for box in r.boxes:
                                cls_name = r.names[int(box.cls[0])]
                                conf = float(box.conf[0])
                                xyxy = box.xyxy[0].tolist()
                                nx1, ny1, nx2, ny2 = xyxy[0]/frame_w, xyxy[1]/frame_h, xyxy[2]/frame_w, xyxy[3]/frame_h

                                frame_detections.append({
                                    'frame_time': round(current_time_sec, 2),
                                    'class_name': cls_name,
                                    'confidence': round(conf, 4),
                                    'x1': round(nx1, 4), 'y1': round(ny1, 4),
                                    'x2': round(nx2, 4), 'y2': round(ny2, 4)
                                })

                                if 'pothole' in cls_name.lower():
                                    is_damaged = True
                                    total_pothole_count += 1
                                    frame_pothole_count += 1
                                    if conf > pothole_max_conf:
                                        pothole_max_conf = conf
                                elif 'sinkhole' in cls_name.lower():
                                    is_damaged = True
                                    sinkhole_count += 1

                                if conf > max_conf:
                                    max_conf, damage_type = conf, cls_name
                                if conf > best_conf:
                                    best_conf = conf
                                    best_frame = frame.copy()
                                    best_result = results[0]

                            if frame_pothole_count > max_pothole_in_frame:
                                max_pothole_in_frame = frame_pothole_count

                frame_idx += 1
                # 혹시 너무 길어지는걸 방지하기 위해 1.5분(2700프레임) 단위로 자르기
                if frame_idx >= 2700:
                    break

            cap.release()
            out.release()
            print(f"[AI Video] Analyzed {frame_idx} frames. Detections={len(frame_detections)}, Pothole={total_pothole_count}, Sinkhole={sinkhole_count}")
            print(f"[AI Video] Output video saved to {output_abs_path}")

            encoded_video_path = f'/uploads/videos/{output_filename}'

            # 프레임별 검출 결과를 DB에 일괄 저장
            if frame_detections:
                with app.app_context():
                    for det in frame_detections:
                        db.session.add(VideoDetection(
                            report_id=report_id,
                            frame_time=det['frame_time'],
                            class_name=det['class_name'],
                            confidence=det['confidence'],
                            x1=det['x1'], y1=det['y1'],
                            x2=det['x2'], y2=det['y2']
                        ))
                    db.session.commit()
                    print(f"[AI Video] Saved {len(frame_detections)} detections to DB")

            # 가장 높은 신뢰도 프레임을 AI 결과 썸네일로 저장
            if best_result is not None and best_frame is not None:
                annotated_filename = f"{name}_ai.jpg"
                annotated_abs = os.path.join(base_dir, 'uploads', 'images', annotated_filename)
                os.makedirs(os.path.dirname(annotated_abs), exist_ok=True)
                cv2.imwrite(annotated_abs, best_result.plot())
                annotated_path = f'/uploads/images/{annotated_filename}'
                print(f"[AI Video] Best frame saved: {annotated_path}")

        else:
            # === 이미지 분석 ===
            results = model(abs_path, verbose=False)

            for r in results:
                if len(r.boxes) > 0:
                    frame_pothole_count = 0
                    for box in r.boxes:
                        cls_name = r.names[int(box.cls[0])]
                        conf = float(box.conf[0])
                        if 'pothole' in cls_name.lower():
                            is_damaged = True
                            total_pothole_count += 1
                            frame_pothole_count += 1
                            if conf > pothole_max_conf: pothole_max_conf = conf
                        elif 'sinkhole' in cls_name.lower():
                            is_damaged = True
                            sinkhole_count += 1

                        if conf > max_conf: max_conf, damage_type = conf, cls_name

                    if frame_pothole_count > max_pothole_in_frame:
                        max_pothole_in_frame = frame_pothole_count

            if (is_damaged or (len(results) > 0 and len(results[0].boxes) > 0)):
                name = os.path.splitext(os.path.basename(abs_path))[0]
                annotated_filename = f"{name}_ai.jpg"
                annotated_abs = os.path.join(os.path.dirname(abs_path), annotated_filename)
                cv2.imwrite(annotated_abs, results[0].plot())
                annotated_path = f'/uploads/images/{annotated_filename}'

        with app.app_context():
            rpt = Report.query.get(report_id)
            if rpt:
                db.session.add(AiResult(report_id=report_id, is_damaged=is_damaged, confidence=round(max_conf * 100, 1), damage_type=damage_type))
                if annotated_path:
                    rpt.thumbnail_path = annotated_path  # 원본 경로는 보존하되 새로 갱신

                # 핵심: CV 박스가 그려져 재인코딩된 영상이 있다면 원본을 덮어써서 프론트에서 재생하게 함
                if file_type == 'video' and encoded_video_path:
                    rpt.file_path = encoded_video_path

                # AI 분석 승인 조건: (포트홀 60% 이상) OR (단일 프레임 포트홀 3개 이상) OR (싱크홀 1개 이상)
                is_valid_report = (pothole_max_conf >= 0.6) or (max_pothole_in_frame >= 3) or (sinkhole_count > 0)

                if is_valid_report:
                    rpt.status = '관리자 확인중'
                    # AI 분석 통과 보상 (+10점)
                    mbr = Member.query.get(rpt.user_id)
                    if mbr:
                        mbr.points += 10
                        db.session.add(PointLog(user_id=rpt.user_id, amount=10, reason='AI 분석 통과 (유효한 제보)'))
                else:
                    rpt.status = '반려'
                    if total_pothole_count == 0 and sinkhole_count == 0:
                        rpt.reject_reason = 'AI 분석 결과 도로 파손(포트홀/싱크홀)이 감지되지 않았습니다. 다시 정확하게 촬영해주세요.'
                    else:
                        rpt.reject_reason = 'AI 분석 결과 도로 파손 유효성 기준(포트홀 신뢰도 60% 미만 등)에 미달했습니다. 명확하게 다시 촬영해주세요.'
                db.session.commit()
    except Exception as e:
        print(f"AI Analysis Error: {e}")
