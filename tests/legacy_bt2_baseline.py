import csv
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from denso_a3.bt2.baseline import BaselineExtractor
from denso_a3.bt2.cli import main
from denso_a3.bt2.input_adapter import load_page_inputs
from denso_a3.bt2.pdf_reader import read_pdf_text, render_pdf_page
from denso_a3.bt2.tesseract import TesseractOCR, parse_tsv
from denso_a3.contracts import PageInput, TextRead


class BaselineTests(unittest.TestCase):
    def test_dataset_adapter_omits_questions_and_answers(self):
        path = Path(__file__).resolve().parents[1] / "evaluation/data/dataset_test.json"
        pages = load_page_inputs(str(path), dataset=True)
        self.assertEqual(len(pages), 50)
        self.assertTrue(all(Path(page.source_path).is_file() for page in pages))
        self.assertTrue(all(page.page_type == "image" for page in pages))
        self.assertTrue(all(set(page.metadata) == {"record_id"} for page in pages))

    def test_tesseract_tsv_preserves_lines_boxes_and_score(self):
        columns = ["level", "page_num", "block_num", "par_num", "line_num",
                   "word_num", "left", "top", "width", "height", "conf", "text"]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for line, word, left, confidence in [(1, "Raw", 10, 90), (1, "79.23%", 50, 80),
                                            (2, "Imported", 10, 70)]:
            writer.writerow(dict(level=5, page_num=1, block_num=1, par_num=1,
                                 line_num=line, word_num=1, left=left, top=line * 20,
                                 width=30, height=12, conf=confidence, text=word))
        read = parse_tsv(buffer.getvalue())
        self.assertEqual(read.text, "Raw 79.23%\nImported")
        self.assertEqual(read.spans[1]["bbox"], [50, 20, 80, 32])
        self.assertEqual([span["line_id"] for span in read.spans], [0, 0, 1])
        self.assertAlmostEqual(read.score, 0.8)

    def test_tesseract_engine_calls_executable_once(self):
        tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv += "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t90\tSQL\n"
        completed = subprocess.CompletedProcess([], 0, stdout=tsv, stderr="")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "sample.png"
            image.write_bytes(b"mock input")
            with patch("denso_a3.bt2.tesseract.shutil.which", return_value="tesseract"), \
                    patch("denso_a3.bt2.tesseract.subprocess.run", return_value=completed) as run:
                read = TesseractOCR().read_image(image)
            self.assertEqual(read.text, "SQL")
            self.assertEqual(run.call_count, 1)

    def test_pdf_reader_returns_text_boxes_and_render_dimensions(self):
        class FakePage:
            def get_text(self, mode, sort=False):
                return {"blocks": [{"lines": [{"spans": [
                    {"text": "FOREIGN KEY", "bbox": [1, 2, 80, 12]}
                ]}]}]}

            def get_pixmap(self, matrix, alpha=False):
                return types.SimpleNamespace(
                    width=120, height=180,
                    save=lambda path: Path(path).write_bytes(b"rendered")
                )

        class FakeDocument:
            def __init__(self):
                self.closed = False

            def __len__(self):
                return 1

            def __getitem__(self, index):
                return FakePage()

            def close(self):
                self.closed = True

        documents = []

        def open_document(path):
            document = FakeDocument()
            documents.append(document)
            return document

        fake_pymupdf = types.SimpleNamespace(open=open_document, Matrix=lambda x, y: (x, y))
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            pdf.write_bytes(b"mock pdf")
            png = Path(directory) / "page.png"
            with patch.dict(sys.modules, {"pymupdf": fake_pymupdf}):
                read = read_pdf_text(str(pdf), 1)
                dimensions = render_pdf_page(str(pdf), 1, 200, png)
            self.assertTrue(png.is_file())
        self.assertEqual(read.text, "FOREIGN KEY")
        self.assertEqual(read.spans[0]["bbox"], [1.0, 2.0, 80.0, 12.0])
        self.assertEqual(dimensions["render_width"], 120)
        self.assertTrue(all(document.closed for document in documents))

    def test_pdf_routes_and_one_page_failure_isolation(self):
        direct = TextRead("FOREIGN KEY", [{"text": "FOREIGN KEY", "bbox": [1, 2, 3, 4],
                                           "score": None, "line_id": 0}],
                          None, "not_applicable", "pdf_points")
        ocr_read = TextRead("OCR text", [], 0.8,
                            "tesseract_mean_word_confidence_uncalibrated", "source_image_pixels")
        pdf_text = PageInput("doc", "sample.pdf", 1, "pdf_text")
        pdf_scan = PageInput("doc", "sample.pdf", 2, "pdf_scan")
        missing = PageInput("bad", "missing.png", None, "image")
        extractor = BaselineExtractor(ocr=TesseractOCR())
        with patch("denso_a3.bt2.baseline.read_pdf_text", return_value=direct), \
                patch("denso_a3.bt2.baseline.render_pdf_page", return_value={
                    "render_dpi": 200, "render_width": 100, "render_height": 200
                }) as render, \
                patch.object(TesseractOCR, "read_image", side_effect=[
                    ocr_read, FileNotFoundError("missing.png")
                ]):
            results = extractor.extract_pages([pdf_text, pdf_scan, missing])
        self.assertEqual([result.status for result in results], ["ok", "ok", "failed"])
        self.assertEqual(results[0].method, "direct_pdf")
        self.assertEqual(results[0].metadata["bbox_space"], "pdf_points")
        self.assertEqual(results[1].method, "ocr")
        self.assertEqual(results[1].metadata["bbox_space"], "rendered_image_pixels")
        self.assertEqual(render.call_count, 1)
        self.assertIn("FileNotFoundError", results[2].error)

    def test_empty_pdf_text_layer_uses_ocr(self):
        empty = TextRead("", [], None, "not_applicable", "pdf_points")
        ocr_read = TextRead("OCR text", [], 0.8,
                            "tesseract_mean_word_confidence_uncalibrated", "source_image_pixels")
        item = PageInput("doc", "sample.pdf", 1, "pdf_text")
        with patch("denso_a3.bt2.baseline.read_pdf_text", return_value=empty), \
                patch("denso_a3.bt2.baseline.render_pdf_page", return_value={}), \
                patch.object(TesseractOCR, "read_image", return_value=ocr_read):
            result = BaselineExtractor().extract_page(item)
        self.assertEqual(result.text, "OCR text")
        self.assertEqual([attempt["method"] for attempt in result.attempts], ["direct_pdf", "ocr"])
        self.assertIn("empty_text_layer", result.quality_flags)

    def test_cli_writes_one_result_per_dataset_image(self):
        dataset = Path(__file__).resolve().parents[1] / "evaluation/data/dataset_test.json"
        read = TextRead("sample text", [], 0.75,
                        "tesseract_mean_word_confidence_uncalibrated", "source_image_pixels")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "baseline.json"
            with patch.object(TesseractOCR, "read_image", return_value=read):
                code = main(["--input", str(dataset), "--dataset", "--output", str(output)])
            results = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(len(results), 50)
        self.assertTrue(all(result["status"] == "ok" for result in results))
        self.assertFalse(any("answers" in result or "question" in result for result in results))


if __name__ == "__main__":
    unittest.main()
