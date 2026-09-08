# Moon impact-crater context

MotherWorld uses Stuart J. Robbins' global lunar impact-crater catalog for the compact Moon Overview statistics.

Published catalog facts used by the UI:

- 2,033,574 craters were identified in the full study database.
- 1,296,879 have diameters >= 1 km.
- approximately 83,000 have diameters >= 5 km.
- 6,972 have diameters >= 20 km.
- the catalog is estimated to be approximately complete for craters larger than roughly 1-2 km, with local variation in the completeness threshold.

The public USGS Astrogeology / NASA PDS archive is the Robbins Moon Crater Database v1. MotherWorld deliberately labels the headline value **Catalogued Craters >=1 km** rather than "total craters on the Moon" because enormous numbers of smaller craters exist below the catalog's completeness/release threshold.

The compact UI constants live in `frontend/public/js/moonImpactStats.js`. They are descriptive Moon context and have zero Earth Health weight.

Primary publication: Robbins, S. J. (2019), *A New Global Database of Lunar Impact Craters >1-2 km: 1. Crater Locations and Sizes, Comparisons With Published Databases, and Global Analysis*, Journal of Geophysical Research: Planets, DOI `10.1029/2018JE005592`.
