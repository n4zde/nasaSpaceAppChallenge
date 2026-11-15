# Approximate bounding box covering the Türkiye detections in the 2018-2020 datasets.
PILOT_BBOX = (25.5, 35.5, 45.0, 42.5)

PRESETS = [
    {
        "id": "pilot-turkiye",
        "name": "Pilot: Türkiye",
        "description": "Türkiye-wide pilot study using real MODIS and Suomi-NPP VIIRS 375 m fire detections from 2018 to 2020. The rectangular study area approximates the geographic coverage of the imported datasets; it is not an exact national boundary.",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [25.5, 35.5], [45.0, 35.5],
                [45.0, 42.5], [25.5, 42.5],
                [25.5, 35.5]
            ]]
        },
        "pilot": True,
    },
    {
        "id": "pilot-turkiye-irregular",
        "name": "Pilot sub-area: Central Türkiye",
        "description": "Illustrative irregular sub-area in central Türkiye, used to demonstrate exact polygon filtering.",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [32.0, 37.0], [34.5, 37.2],
                [35.5, 38.5], [35.0, 39.8],
                [33.5, 40.5], [31.8, 39.8],
                [31.5, 38.5], [32.0, 37.0]
            ]]
        },
        "pilot": True,
    },
    {
        "id": "top-end-australia",
        "name": "Top End, Northern Territory, Australia",
        "description": "Tropical savanna with a strong early/late dry-season fire regime.",
        "geometry": {"type": "Polygon", "coordinates": [[[130.0, -15.5], [136.5, -15.5], [136.5, -12.0], [130.0, -12.0], [130.0, -15.5]]]},
    },
    {
        "id": "sierra-foothills-california",
        "name": "Sierra Nevada foothills, California, USA",
        "description": "Mediterranean-climate wildfire region.",
        "geometry": {"type": "Polygon", "coordinates": [[[-122.0, 37.0], [-119.0, 37.0], [-119.0, 40.5], [-122.0, 40.5], [-122.0, 37.0]]]},
    },
    {
        "id": "chukotka-antimeridian",
        "name": "Chukotka across the antimeridian (test case)",
        "description": "Crosses 180 degrees longitude; demonstrates explicit antimeridian splitting.",
        "geometry": {"type": "Polygon", "coordinates": [[[172.0, 63.0], [-172.0, 63.0], [-172.0, 67.0], [172.0, 67.0], [172.0, 63.0]]]},
    },
]

PRESET_BY_ID = {p["id"]: p for p in PRESETS}
