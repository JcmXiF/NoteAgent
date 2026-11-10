"""The agent kernel: contracts the loop runs on, independent of any business.

Nothing in this package imports the wiki, ingestion, acquisition or the old chat:
the kernel is the reusable part, and the product supplies adapters at its seams.
"""
