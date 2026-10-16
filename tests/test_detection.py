import unittest

import numpy as np

from byebike.detection import EncroachmentTracker, contact_fraction, pair_cyclists, validate_zones, zone_masks


def area(kind, left=0, right=1):
    return {"kind": kind, "points": [[left, 0], [right, 0], [right, 1], [left, 1]]}


def cyclist(box=(30, 30, 70, 80)):
    return {"bike_box": list(box), "box": list(box), "person_box": [35, 10, 65, 75], "confidence": 0.9}


class GeometryTests(unittest.TestCase):
    def test_shared_boundary_is_allowed_but_overlapping_interiors_are_not(self):
        self.assertEqual(len(validate_zones([area("cycling", 0, .5), area("pedestrian", .5, 1)])), 2)
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_zones([area("cycling", 0, .6), area("pedestrian", .5, 1)])

    def test_invalid_shapes_and_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_zones([{"kind":"pedestrian", "points":[[0,0],[1,1],[0,1],[1,0]]}])
        for bad in (float("nan"), float("inf"), -0.1, 1.1):
            with self.assertRaises(ValueError):
                validate_zones([{"kind":"pedestrian", "points":[[bad,0],[1,0],[1,1]]}])

    def test_contact_overlap_uses_ground_edge_not_upper_body(self):
        mask = np.zeros((100, 100), np.uint8)
        mask[:60] = 1
        self.assertEqual(contact_fraction([20,10,80,90], mask), 0)
        mask[:,50:] = 1
        self.assertAlmostEqual(contact_fraction([20,10,80,90], mask), 11 / 21)

    def test_polygon_rescales_with_camera_resolution(self):
        for size in (100, 400):
            mask = zone_masks([area("pedestrian",.5,1)], (size,size))["pedestrian"]
            self.assertEqual(mask[size//2,size//4], 0)
            self.assertEqual(mask[size//2,size*3//4], 1)

    def test_person_and_bicycle_are_both_required(self):
        bike = {"class_id":1,"box":[10,30,90,90],"confidence":.8}
        rider = {"class_id":0,"box":[30,5,60,85],"confidence":.9}
        bystander = {"class_id":0,"box":[100,10,120,80],"confidence":.9}
        self.assertEqual(pair_cyclists([bike]), [])
        self.assertEqual(pair_cyclists([rider]), [])
        self.assertEqual(pair_cyclists([bike,bystander]), [])
        pairs = pair_cyclists([bike,rider,rider])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["box"], [10,5,90,90])


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = EncroachmentTracker()
        self.masks = zone_masks([area("cycling",0,.5),area("pedestrian",.5,1)], (100,100))

    def update(self, now, box=(30,30,70,80), **rules):
        return self.tracker.update([cyclist(box)], self.masks, now, **rules)[0]

    def test_dwell_and_repeated_frames_produce_one_event(self):
        self.assertFalse(self.update(0)["event"])
        self.assertFalse(self.update(.6)["event"])
        self.assertTrue(self.update(.8)["event"])
        self.assertFalse(self.update(1.5)["event"])
        self.assertTrue(self.update(1.6)["confirmed"])

    def test_user_threshold_changes_decision(self):
        self.assertFalse(self.update(0,overlap=.75)["inside"])
        self.assertFalse(self.update(1,overlap=.75)["event"])
        self.assertTrue(self.update(2,overlap=.25)["inside"])
        self.assertTrue(self.update(3,overlap=.25)["event"])

    def test_jitter_and_missing_detections_reset_confirmation(self):
        self.update(0)
        self.update(.5,box=(10,30,49,80))
        self.assertFalse(self.update(.8)["event"])
        self.tracker.update([],self.masks,1)
        self.assertFalse(self.update(1.6)["event"])
        self.assertTrue(self.update(2.4)["event"])

    def test_reentry_rearms_only_after_time_outside(self):
        self.update(0)
        self.assertTrue(self.update(.8)["event"])
        for t in (1,2,3,4.1):
            self.update(t,box=(10,30,49,80))
        self.assertFalse(self.update(4.2)["event"])
        event = self.update(5)
        self.assertTrue(event["event"])
        self.assertEqual(event["event_type"], "crossed_from_cycling")

    def test_multiple_cyclists_keep_distinct_tracks(self):
        first = self.tracker.update([cyclist((10,10,30,35)),cyclist((60,50,95,90))],self.masks,0)
        later = self.tracker.update([cyclist((60,50,95,90)),cyclist((10,10,30,35))],self.masks,1)
        self.assertEqual([c["track_id"] for c in first],list(reversed([c["track_id"] for c in later])))
        self.assertTrue(later[0]["event"])
        self.assertFalse(later[1]["event"])


if __name__ == "__main__":
    unittest.main()
