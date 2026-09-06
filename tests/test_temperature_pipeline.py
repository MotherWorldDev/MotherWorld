import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import xarray as xr
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from temperature_common import (accumulate_window, canonicalize_grid, daily_offsets, fractional_weight_window,
    grid_cell_area_rows, load_land_geometries, load_lake_geometries, load_marine_geometries,
    make_bin_edges, open_downloaded_dataset, processing_fingerprint, temperature_to_c,
    update_climate_index, weighted_stats_from_hist)
from build_marine_temperature import build_weight_table, process_year, selected_geometries


class TemperaturePipelineTests(unittest.TestCase):
    def test_area_day_weighting_and_daily_means_are_distinct(self):
        values = np.array([[0., 10.], [20., np.nan]])
        num, den, hist, total, squared = accumulate_window(values, np.array([1., 3.]), np.array([-5., 5., 15., 25.]))
        np.testing.assert_allclose(num / den, [7.5, 20.])
        np.testing.assert_allclose(hist, [1, 3, 1])
        self.assertEqual(total, 50)
        self.assertEqual(squared, 700)
        stats = weighted_stats_from_hist(hist, np.array([-5., 5., 15., 25.]), total, squared)
        self.assertEqual(stats['meanC'], 10)
        self.assertAlmostEqual(stats['stdC'], np.sqrt(40))
        self.assertTrue(stats['quantilesApproximate'])
        with self.assertRaisesRegex(ValueError, 'outside histogram'):
            accumulate_window(values, np.array([1., 3.]), np.array([0., 5., 15.]))

    def test_fractional_weights_and_spherical_area(self):
        lat = np.array([1.5, .5, -.5]); lon = np.array([.5, 1.5, 2.5])
        window = fractional_weight_window(box(0, 0, .5, 1), lat, lon, 4)
        area = grid_cell_area_rows(np.array([.5]), 1, 1)[0]
        self.assertAlmostEqual(window.weights.sum() / area, .5)
        polar, equator = grid_cell_area_rows(np.array([60., 0.]), 1, 1)
        self.assertAlmostEqual(polar / equator, .5)

    def test_open_ocean_filter_keeps_all_mapped_boundaries(self):
        geoms = {'marine_a': box(0, 0, 1, 1), 'marine_b': box(1, 0, 2, 1)}
        selected = selected_geometries(geoms, {'open_ocean'}, True)
        self.assertEqual(set(selected), set(geoms))
        lat, lon = np.array([1.5, .5]), np.array([.5, 1.5])
        table = build_weight_table(selected, lat, lon, 4, True)
        self.assertEqual(set(table['open_ocean'][0]), {0, 1})
        self.assertEqual(set(selected_geometries(geoms, {'marine_a'}, False)), {'marine_a'})

    def test_units_dates_and_bins_reject_silent_corruption(self):
        np.testing.assert_allclose(temperature_to_c(np.array([273.15, 283.15]), 'K'), [0, 10])
        with self.assertRaises(ValueError): temperature_to_c(np.array([30]), 'fahrenheit')
        np.testing.assert_array_equal(daily_offsets(['2020-01-01', '2020-03-01'], 2020), [0, 60])
        for dates in (['2019-01-01'], ['2020-01-01', '2020-01-01'], ['NaT']):
            with self.assertRaises(ValueError): daily_offsets(dates, 2020)
        with self.assertRaises(ValueError): make_bin_edges(0, 10, 3)

    def test_cds_archive_combines_all_months_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'download.bin'
            files = []
            for month in (1, 2):
                path = root / f'{month}.nc'
                xr.Dataset({'t2m': (('time', 'latitude', 'longitude'), np.full((1, 2, 2), 273.15 + month))},
                    coords={'time': [np.datetime64(f'2019-{month:02d}-01')], 'latitude': [1., 0.], 'longitude': [0., 1.]}).to_netcdf(path)
                files.append(path)
            with zipfile.ZipFile(archive, 'w') as z:
                for file in files: z.write(file, file.name)
            dataset, extracted = open_downloaded_dataset(archive)
            try:
                self.assertEqual(dataset.sizes['time'], 2)
                np.testing.assert_allclose(dataset.t2m[:, 0, 0], [274.15, 275.15])
            finally:
                dataset.close(); extracted.cleanup()
            with zipfile.ZipFile(archive, 'w') as z: z.write(files[0], '../escape.nc')
            with self.assertRaisesRegex(ValueError, 'Unsafe'): open_downloaded_dataset(archive)

    def test_marine_year_handles_reordered_dimensions_and_missing_cells(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'year.nc'
            # Native layout differs from the canonical time/lat/lon order.
            values = np.array([[[10, 20], [0, 10]], [[20, 30], [np.nan, 20]]])
            ds = xr.Dataset({'sst': (('time', 'lat', 'lon'), values)},
                coords={'time': np.array(['2019-01-01', '2019-01-02'], dtype='datetime64[D]'), 'lat': [1.5, .5], 'lon': [.5, 1.5]})
            ds.sst.attrs['units'] = 'degC'
            ds.transpose('lon', 'time', 'lat').to_netcdf(path)
            result = process_year(path, 2019, {'marine_a': (np.array([2, 3]), np.array([1., 3.]))},
                np.array([1.5, .5]), np.array([.5, 1.5]), np.arange(-5, 36, 10), 1)
            np.testing.assert_allclose(result['daily'][0], [7.5, 20.])
            self.assertAlmostEqual(result['hist'].sum(), 7)
            self.assertAlmostEqual(result['moments'][0, 0], 90)

    def test_grid_longitudes_and_manifest_merging(self):
        ds = xr.Dataset(coords={'lat': [-.5, .5], 'lon': [0., 90., 180., 270.]})
        grid, _, _ = canonicalize_grid(ds)
        np.testing.assert_array_equal(grid.lon, [-180, -90, 0, 90])
        np.testing.assert_array_equal(grid.lat, [.5, -.5])
        with tempfile.TemporaryDirectory() as temporary:
            import json
            repo = Path(temporary)
            update_climate_index(repo, {'eco_1': {'url': 'land/eco_1.temperature.json'}}, {'era5-land': {}})
            update_climate_index(repo, {'marine_a': {'url': 'marine/marine_a.temperature.json'}}, {'oisst': {}})
            manifest = json.loads((repo/'frontend/public/data/climate/climate.index.json').read_text())
            self.assertEqual(set(manifest['regions']), {'eco_1', 'marine_a'})
            self.assertEqual(set(manifest['sources']), {'era5-land', 'oisst'})
        a = processing_fingerprint({'supersample': 4}, {'eco_a': box(0,0,1,1)})
        self.assertNotEqual(a, processing_fingerprint({'supersample': 8}, {'eco_a': box(0,0,1,1)}))

    def test_land_builder_writes_real_units_and_resumes_completed_year(self):
        import json
        from unittest.mock import patch
        import build_land_temperature as land
        calls = []
        class Client:
            def retrieve(self, dataset, request, target):
                calls.append(request)
                values = np.array([[[np.nan, np.nan], [273.15, 283.15]], [[np.nan, np.nan], [293.15, np.nan]]])
                ds = xr.Dataset({'t2m': (('time', 'latitude', 'longitude'), values)},
                    coords={'time': np.array(['2019-01-01','2019-01-03'], dtype='datetime64[D]'),
                            'latitude': [1.5,.5], 'longitude': [.5,1.5]})
                ds.t2m.attrs['units'] = 'K'
                ds.to_netcdf(target)
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            root = repo / 'frontend/public/data'; root.mkdir(parents=True)
            (root/'regions.index.json').write_text(json.dumps({'regions':{'eco_1':{'name':'Test'}}}))
            (root/'lakes.index.json').write_text('{"regions":{}}')
            argv = ['builder', '--repo', str(repo), '--region','eco_1','--years','2019','--chunk-days','1']
            with patch.object(sys, 'argv', argv), patch.object(land, 'load_land_geometries', return_value={'eco_1':box(0,0,2,1)}), patch.object(land, 'load_lake_geometries', return_value={}), patch.object(land.cdsapi, 'Client', Client):
                land.main()
                output = root/'climate/land/eco_1.temperature.json'
                data = json.loads(output.read_text())
                self.assertEqual(data['distributions']['regionalDailyMean']['sampleCountDays'], 2)
                self.assertAlmostEqual(data['distributions']['regionalDailyMean']['stats']['meanC'],12.5)
                self.assertAlmostEqual(data['distributions']['spaceTime']['stats']['meanC'],10.)
                self.assertEqual(len(calls),1)
                self.assertFalse(list((repo/'.cache/motherworld/climate/raw').rglob('*.zip')))
                output.unlink()  # Recreate a missing release using the processed checkpoint.
                land.main()
                self.assertEqual(len(calls),1)
                self.assertEqual(json.loads(output.read_text())['distributions'],data['distributions'])

    def test_committed_geometry_preserves_every_logical_region(self):
        repo = Path(__file__).resolve().parents[1]
        import json
        for loader, index in ((load_land_geometries, 'regions'), (load_lake_geometries, 'lakes'), (load_marine_geometries, 'marine')):
            expected = json.loads((repo / f'frontend/public/data/{index}.index.json').read_text(encoding='utf-8'))['regions']
            self.assertEqual(set(loader(repo)), set(expected))


if __name__ == '__main__': unittest.main()
