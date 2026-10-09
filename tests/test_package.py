from importlib.metadata import version

import flagstones


def test_the_package_reports_its_installed_version():
    assert flagstones.__version__ == version("flagstones")


def test_the_published_surface_is_exactly_the_documented_names():
    assert set(flagstones.__all__) == {
        "generate_tiles",
        "TileParams",
        "TileSet",
        "Tile",
        "Cluster",
        "FallbackGrid",
        "SeedCollisionError",
        "PreConsolidatedGraphError",
        "NonDrivableGraphError",
        "AnisotropicCrsError",
        "CrsScaleError",
    }
    assert isinstance(flagstones.__version__, str)
