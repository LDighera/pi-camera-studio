from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import numpy as np

try:
    import cv2
except ImportError:  # The UI explains how to install the optional dependency.
    cv2 = None


# COCO class order used by the OpenCV Zoo NanoDet model.
COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
)


class DetectorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Detection:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    class_id: int

    @property
    def label(self) -> str:
        if 0 <= self.class_id < len(COCO_CLASSES):
            return COCO_CLASSES[self.class_id]
        return f"class {self.class_id}"


@dataclass(frozen=True)
class DetectionResult:
    detections: tuple[Detection, ...]
    inference_ms: float
    frame_size: tuple[int, int]


def letterbox(image: np.ndarray, target_size: tuple[int, int] = (416, 416)) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if cv2 is None:
        raise DetectorUnavailable("Python OpenCV is not installed")
    target_h, target_w = target_size
    source_h, source_w = image.shape[:2]
    scale = min(target_w / source_w, target_h / source_h)
    new_w = max(1, int(round(source_w * scale)))
    new_h = max(1, int(round(source_h * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    left = (target_w - new_w) // 2
    right = target_w - new_w - left
    top = (target_h - new_h) // 2
    bottom = target_h - new_h - top
    boxed = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=0)
    return boxed, (top, left, new_h, new_w)


def unletterbox_box(
    box: np.ndarray,
    original_shape: tuple[int, int],
    letterbox_geometry: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    source_h, source_w = original_shape
    top, left, new_h, new_w = letterbox_geometry
    x1 = int(np.clip((box[0] - left) * source_w / new_w, 0, source_w - 1))
    y1 = int(np.clip((box[1] - top) * source_h / new_h, 0, source_h - 1))
    x2 = int(np.clip((box[2] - left) * source_w / new_w, 0, source_w - 1))
    y2 = int(np.clip((box[3] - top) * source_h / new_h, 0, source_h - 1))
    return x1, y1, x2, y2


class NanoDetDetector:
    """OpenCV Zoo NanoDet inference, adapted from its Apache-2.0 demo."""

    image_shape = (416, 416)
    # The pinned OpenCV Zoo model exposes three score/box pairs (52², 26²,
    # and 13² locations), corresponding to strides 8, 16, and 32.
    strides = (8, 16, 32)
    reg_max = 7

    def __init__(self, model_path: Path, confidence: float = 0.35, nms_iou: float = 0.6):
        if cv2 is None:
            raise DetectorUnavailable("Install the Debian package python3-opencv")
        if not model_path.is_file():
            raise DetectorUnavailable(f"Object model not found: {model_path}")
        self.model_path = model_path
        self.confidence = confidence
        self.nms_iou = nms_iou
        self.project = np.arange(self.reg_max + 1)
        self.mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)
        self.net = cv2.dnn.readNet(str(model_path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.anchors = [self._make_anchors(stride) for stride in self.strides]

    def _make_anchors(self, stride: int) -> np.ndarray:
        feature_h = self.image_shape[0] // stride
        feature_w = self.image_shape[1] // stride
        xv, yv = np.meshgrid(np.arange(feature_w) * stride, np.arange(feature_h) * stride)
        cx = xv.flatten() + 0.5 * (stride - 1)
        cy = yv.flatten() + 0.5 * (stride - 1)
        return np.column_stack((cx, cy))

    def detect(self, frame_bgr: np.ndarray, confidence: float | None = None) -> DetectionResult:
        threshold = self.confidence if confidence is None else confidence
        original_shape = frame_bgr.shape[:2]
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        boxed, geometry = letterbox(frame_rgb, self.image_shape)
        normalized = (boxed.astype(np.float32) - self.mean) / self.std
        blob = cv2.dnn.blobFromImage(normalized)
        self.net.setInput(blob)
        start = monotonic()
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        inference_ms = (monotonic() - start) * 1000
        self._validate_outputs(outputs)
        raw = self._postprocess(outputs, threshold)
        detections = []
        for row in raw:
            x1, y1, x2, y2 = unletterbox_box(row[:4], original_shape, geometry)
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(
                Detection(x1, y1, x2, y2, float(row[4]), int(row[5]))
            )
        return DetectionResult(tuple(detections), inference_ms, (original_shape[1], original_shape[0]))

    def _validate_outputs(self, outputs: list[np.ndarray]) -> None:
        expected_outputs = 2 * len(self.strides)
        if len(outputs) != expected_outputs:
            raise DetectorUnavailable(
                f"NanoDet returned {len(outputs)} outputs; expected {expected_outputs}"
            )
        for level, (scores, regression, anchors) in enumerate(
            zip(outputs[::2], outputs[1::2], self.anchors)
        ):
            scores = np.squeeze(scores, axis=0) if scores.ndim == 3 else scores
            regression = np.squeeze(regression, axis=0) if regression.ndim == 3 else regression
            if scores.ndim != 2 or scores.shape != (len(anchors), len(COCO_CLASSES)):
                raise DetectorUnavailable(
                    f"Unexpected NanoDet score shape at level {level}: {scores.shape}"
                )
            if regression.ndim != 2 or regression.shape != (len(anchors), 4 * (self.reg_max + 1)):
                raise DetectorUnavailable(
                    f"Unexpected NanoDet box shape at level {level}: {regression.shape}"
                )

    def _postprocess(self, outputs: list[np.ndarray], threshold: float) -> np.ndarray:
        class_scores = outputs[::2]
        bbox_predictions = outputs[1::2]
        boxes_by_level: list[np.ndarray] = []
        scores_by_level: list[np.ndarray] = []

        for stride, scores, bbox, anchors in zip(self.strides, class_scores, bbox_predictions, self.anchors):
            scores = np.squeeze(scores, axis=0) if scores.ndim == 3 else scores
            bbox = np.squeeze(bbox, axis=0) if bbox.ndim == 3 else bbox
            distribution = bbox.reshape(-1, self.reg_max + 1)
            distribution -= distribution.max(axis=1, keepdims=True)
            distribution = np.exp(distribution)
            distribution /= distribution.sum(axis=1, keepdims=True)
            distances = np.dot(distribution, self.project).reshape(-1, 4) * stride

            if scores.shape[0] > 1000:
                top_indices = scores.max(axis=1).argsort()[::-1][:1000]
                scores = scores[top_indices]
                distances = distances[top_indices]
                anchors = anchors[top_indices]

            x1 = np.clip(anchors[:, 0] - distances[:, 0], 0, self.image_shape[1])
            y1 = np.clip(anchors[:, 1] - distances[:, 1], 0, self.image_shape[0])
            x2 = np.clip(anchors[:, 0] + distances[:, 2], 0, self.image_shape[1])
            y2 = np.clip(anchors[:, 1] + distances[:, 3], 0, self.image_shape[0])
            boxes_by_level.append(np.column_stack((x1, y1, x2, y2)))
            scores_by_level.append(scores)

        boxes = np.concatenate(boxes_by_level, axis=0)
        scores = np.concatenate(scores_by_level, axis=0)
        class_ids = np.argmax(scores, axis=1)
        confidences = np.max(scores, axis=1)
        boxes_xywh = boxes.copy()
        boxes_xywh[:, 2:4] -= boxes_xywh[:, 0:2]
        indices = cv2.dnn.NMSBoxes(boxes_xywh.tolist(), confidences.tolist(), threshold, self.nms_iou)
        if len(indices) == 0:
            return np.empty((0, 6), dtype=np.float32)
        indices = np.asarray(indices).reshape(-1)
        return np.column_stack((boxes[indices], confidences[indices], class_ids[indices]))


def render_overlay(frame_size: tuple[int, int], detections: tuple[Detection, ...]) -> np.ndarray:
    if cv2 is None:
        raise DetectorUnavailable("Python OpenCV is not installed")
    width, height = frame_size
    overlay = np.zeros((height, width, 4), dtype=np.uint8)
    for detection in detections:
        colour = (46, 204, 113, 235)  # RGBA; OpenCV writes channel values literally here.
        cv2.rectangle(overlay, (detection.x1, detection.y1), (detection.x2, detection.y2), colour, 2)
        label = f"{detection.label} {detection.confidence:.0%}"
        text_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        label_y = max(detection.y1, text_size[1] + baseline + 4)
        cv2.rectangle(
            overlay,
            (detection.x1, label_y - text_size[1] - baseline - 4),
            (detection.x1 + text_size[0] + 6, label_y + 2),
            (20, 20, 20, 220),
            -1,
        )
        cv2.putText(
            overlay,
            label,
            (detection.x1 + 3, label_y - baseline),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            colour,
            1,
            cv2.LINE_AA,
        )
    return overlay


def annotate_frame(frame_bgr: np.ndarray, detections: tuple[Detection, ...]) -> np.ndarray:
    if cv2 is None:
        raise DetectorUnavailable("Python OpenCV is not installed")
    result = frame_bgr.copy()
    for detection in detections:
        cv2.rectangle(result, (detection.x1, detection.y1), (detection.x2, detection.y2), (60, 210, 80), 2)
        cv2.putText(
            result,
            f"{detection.label} {detection.confidence:.0%}",
            (detection.x1, max(18, detection.y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (60, 210, 80),
            2,
            cv2.LINE_AA,
        )
    return result
