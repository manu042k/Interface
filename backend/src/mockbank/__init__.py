"""MockBank — an intentionally hostile 'legacy' back-office web app.

Stands in for a real core-banking servicing screen: server-rendered, table-based
layout, framesets, nested tables, inline onclick handlers, no test IDs, ugly
form field names. It exposes exactly the flows the assignment cares about:

  search -> results -> member detail (read a balance)
  member detail -> open sub-account (multi-step form) -> confirmation screen

...plus the exceptional states a production replay must handle:

  * "record not found"      -> member id 00000 (or any unknown id)
  * permission denied       -> member id 99999
  * unexpected interstitial -> shown once per session before the detail page
  * transient slow load     -> ?slow=1
  * validation error        -> submitting the sub-account form with no type
"""
