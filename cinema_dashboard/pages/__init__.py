"""Streamlit page modules.

Present only so ``pages`` is a regular package rather than an implicit
namespace one. ``app.py`` imports ``pages.movie`` (the query-parameter-routed
detail view), which makes mypy resolve that file as ``pages.movie`` while the
``mypy pages/`` command-line argument would otherwise resolve the same file as
top-level ``movie`` — "source file found twice under different module names".

Streamlit itself is unaffected: ``st.Page`` executes each page file by path
into a fresh ``__main__`` namespace, never by import.
"""
