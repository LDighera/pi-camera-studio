# Object-detection model

The runtime model is OpenCV Zoo's NanoDet-m-plus-1.5x_416 model, pinned to
revision `f88e9b2bafd21f1cad242fb5af6d78f2bcba16a3`. Ordinary application startup
never downloads or replaces it.

The expected file is `object_detection_nanodet_2022nov.onnx`, exactly 3,800,954
bytes with SHA-256
`4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`.
The application disables only detection if this integrity check fails.

The model and its upstream reference code are Apache-2.0 licensed. See
`LICENSE.NanoDet` and `model.json`.
