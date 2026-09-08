# Inventory Summary Benchmark

Project: `Kothakota to Kurnool` date `2026-07-28`

- Cold aggregate_ms: 2290.72
- Cold total_ms: 2291.75
- Summary JSON bytes: 525
- Summary gzip bytes: 337
- HTTP cold/hit ms: 1118.41 / 187.04
- inventory_filter page bytes: 3824592

## EXPLAIN ANALYZE

```
Finalize Aggregate  (cost=44626.54..44626.55 rows=1 width=240) (actual time=260.222..265.134 rows=1.00 loops=1)
  Buffers: shared hit=13672
  ->  Gather  (cost=44626.18..44626.39 rows=2 width=240) (actual time=259.008..265.118 rows=3.00 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=13672
        ->  Partial Aggregate  (cost=43626.18..43626.19 rows=1 width=240) (actual time=250.326..250.327 rows=1.00 loops=3)
              Buffers: shared hit=13672
              ->  Parallel Bitmap Heap Scan on inventory_wide  (cost=2751.52..38949.18 rows=31180 width=249) (actual time=11.675..53.653 rows=76841.00 loops=3)
                    Recheck Cond: (("Project Name" = 'Kothakota to Kurnool'::text) AND ("Date" = '2026-07-28'::date))
                    Filter: (("Chainage start" >= '0'::double precision) AND ("Chainage end" <= '99999'::double precision))
                    Heap Blocks: exact=4008
                    Buffers: shared hit=13672
                    Worker 0:  Heap Blocks: exact=4024
                    Worker 1:  Heap Blocks: exact=3948
                    ->  Bitmap Index Scan on inventory_wide_project_date_id_idx  (cost=0.00..2732.82 rows=74839 width=0) (actual time=13.047..13.047 rows=230523.00 loops=1)
                          Index Cond: (("Project Name" = 'Kothakota to Kurnool'::text) AND ("Date" = '2026-07-28'::date))
                          Index Searches: 1
                          Buffers: shared hit=1650
Planning:
  Buffers: shared hit=5
Planning Time: 2.218 ms
Execution Time: 265.269 ms
```
