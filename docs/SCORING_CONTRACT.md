# Scoring contract

Every family index follows one direction:

**100 = best condition / lowest pressure**

The deployed contract separates:

1. raw physical measurement
2. normalization rule
3. 0–100 component score
4. component weight
5. family score
6. data coverage / confidence

Within a family, scored components use a weighted geometric mean unless a provider-specific method explicitly says otherwise. Global spatial aggregation of regional condition scores uses area-weighted arithmetic means. Earth Health then uses a weighted geometric mean across family indices.

Missing data never becomes a healthy score. Coverage is carried independently and minimum coverage gates can suppress family or Earth Health publication.
