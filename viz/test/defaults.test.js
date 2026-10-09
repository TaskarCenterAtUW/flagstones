import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DEFAULT_FALLBACK, DEFAULT_TILE_PARAMS } from "../src/app/state.js";

const python = JSON.parse(readFileSync(new URL("./fixtures/tile-params.json", import.meta.url), "utf8"));

test("the viz opens at the library's TileParams defaults", () => {
  const d = DEFAULT_TILE_PARAMS;
  assert.deepEqual(
    {
      consolidation_tolerance_m: d.toleranceM,
      seed_grid_m: d.gridM,
      contract_transitions: d.contractTransitions,
      transition_require_same_road: d.transitionOpts.requireSameRoad,
      transition_spread_max_deg: d.transitionOpts.spreadMaxDeg,
      transition_opposite_min_deg: d.transitionOpts.oppositeMinDeg,
      clip_buffer_m: d.clipBufferM,
    },
    {
      consolidation_tolerance_m: python.consolidation_tolerance_m,
      seed_grid_m: python.seed_grid_m,
      contract_transitions: python.contract_transitions,
      transition_require_same_road: python.transition_require_same_road,
      transition_spread_max_deg: python.transition_spread_max_deg,
      transition_opposite_min_deg: python.transition_opposite_min_deg,
      clip_buffer_m: python.clip_buffer_m,
    },
  );
});

const pythonGrid = JSON.parse(readFileSync(new URL("./fixtures/fallback-grid.json", import.meta.url), "utf8"));

test("the viz opens at the library's FallbackGrid defaults", () => {
  assert.deepEqual({ cell_m: DEFAULT_FALLBACK.cellM, min_share: DEFAULT_FALLBACK.minShare }, pythonGrid);
});
