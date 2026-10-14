# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE
import io
import json

from pypdf import PdfReader, PdfWriter

import frappe
from frappe.tests import IntegrationTestCase

CALLS = []
HANDLER = "frappe.tests.test_after_print"


def mark_before_print(doc, method, *args, **kwargs):
	doc.flags.prepared_for_print = True


def add_page(doc, method, pdf):
	"""Append a 11x11 pt marker page and record what the handler saw."""
	reader = PdfReader(io.BytesIO(pdf))
	CALLS.append(
		frappe._dict(
			name=doc.name,
			handler="add_page",
			encrypted=reader.is_encrypted,
			prepared=bool(doc.flags.prepared_for_print),
		)
	)
	writer = PdfWriter(clone_from=reader)
	writer.add_blank_page(width=11, height=11)
	stream = io.BytesIO()
	writer.write(stream)
	return stream.getvalue()


def keep_pdf(doc, method, pdf):
	CALLS.append(frappe._dict(name=doc.name, handler="keep_pdf", pages=len(PdfReader(io.BytesIO(pdf)).pages)))


def marker_pages(pdf: bytes, password: str | None = None) -> int:
	reader = PdfReader(io.BytesIO(pdf))
	if reader.is_encrypted:
		reader.decrypt(password)
	return sum(1 for page in reader.pages if round(float(page.mediabox.width)) == 11)


class TestAfterPrint(IntegrationTestCase):
	def setUp(self):
		CALLS.clear()
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("test@example.com")
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": "after print"}).insert()

	def handlers(self, *after_print):
		"""Register real doc_events for ToDo for the duration of the test."""
		doc_events = {
			"ToDo": {
				"before_print": [f"{HANDLER}.mark_before_print"],
				"after_print": [f"{HANDLER}.{handler}" for handler in after_print],
			}
		}
		self.enterContext(self.patch_hooks({"doc_events": doc_events}))
		frappe.local.doc_events_hooks = None  # get_doc_hooks() caches per request
		self.addCleanup(setattr, frappe.local, "doc_events_hooks", None)

	def jinja_format(self):
		name = "_Test After Print Jinja"
		if not frappe.db.exists("Print Format", name):
			frappe.get_doc(
				{
					"doctype": "Print Format",
					"name": name,
					"doc_type": "ToDo",
					"custom_format": 1,
					"print_format_type": "Jinja",
					"html": "<p>{{ doc.description }}</p>",
				}
			).insert(ignore_permissions=True)
		return name

	def test_builder_path_runs_once_on_prepared_doc(self):
		self.handlers("add_page")
		pdf = frappe.get_print("ToDo", self.todo.name, as_pdf=True)

		self.assertEqual(marker_pages(pdf), 1)
		self.assertEqual(len(CALLS), 1)
		self.assertTrue(CALLS[0].prepared, "after_print must see the document before_print prepared")
		self.assertFalse(CALLS[0].encrypted)

	def test_classic_path_runs_once_on_prepared_doc(self):
		self.handlers("add_page")
		pdf = frappe.get_print("ToDo", self.todo.name, self.jinja_format(), as_pdf=True)

		self.assertEqual(marker_pages(pdf), 1)
		self.assertEqual(len(CALLS), 1)
		self.assertTrue(CALLS[0].prepared)

	def test_handler_gets_plain_pdf_and_result_is_encrypted(self):
		self.handlers("add_page")
		for print_format in (None, self.jinja_format()):
			CALLS.clear()
			pdf = frappe.get_print("ToDo", self.todo.name, print_format, as_pdf=True, password="qwe")

			self.assertTrue(PdfReader(io.BytesIO(pdf)).is_encrypted)
			self.assertEqual(marker_pages(pdf, "qwe"), 1)
			self.assertEqual([call.encrypted for call in CALLS], [False])

	def test_handlers_are_chained(self):
		self.handlers("add_page", "add_page", "keep_pdf")
		pdf = frappe.get_print("ToDo", self.todo.name, as_pdf=True)

		self.assertEqual(marker_pages(pdf), 2)
		self.assertEqual([call.handler for call in CALLS], ["add_page", "add_page", "keep_pdf"])
		# keep_pdf returns None and sees the output of both handlers before it
		self.assertEqual(CALLS[2].pages, len(PdfReader(io.BytesIO(pdf)).pages))

	def test_attach_print_runs_once(self):
		self.handlers("add_page")
		for print_format in (None, self.jinja_format()):
			CALLS.clear()
			attachment = frappe.attach_print("ToDo", self.todo.name, print_format=print_format)

			self.assertEqual(marker_pages(attachment["fcontent"]), 1)
			self.assertEqual(len(CALLS), 1)

	def test_attach_print_html_runs_once_and_encrypts(self):
		self.handlers("add_page")
		attachment = frappe.attach_print("ToDo", self.todo.name, html="<p>mail</p>", password="qwe")

		self.assertEqual(marker_pages(attachment["fcontent"], "qwe"), 1)
		self.assertEqual([call.encrypted for call in CALLS], [False])

	def test_multi_pdf_with_password_keeps_every_document(self):
		from frappe.utils.print_format import _download_multi_pdf

		self.handlers("add_page")
		other = frappe.get_doc({"doctype": "ToDo", "description": "after print 2"}).insert()
		names = [self.todo.name, other.name]
		for print_format in ("Standard", self.jinja_format()):
			CALLS.clear()
			_download_multi_pdf(
				"ToDo", json.dumps(names), format=print_format, options=json.dumps({"password": "qwe"})
			)

			pdf = frappe.local.response.filecontent
			self.assertTrue(PdfReader(io.BytesIO(pdf)).is_encrypted)
			self.assertEqual(marker_pages(pdf, "qwe"), 2)
			self.assertEqual(sorted(call.name for call in CALLS), sorted(names))

	def test_without_handler_nothing_changes(self):
		self.handlers()
		pdf = frappe.get_print("ToDo", self.todo.name, as_pdf=True, password="qwe")

		self.assertTrue(PdfReader(io.BytesIO(pdf)).is_encrypted)
		self.assertEqual(marker_pages(pdf, "qwe"), 0)
		self.assertEqual(CALLS, [])
