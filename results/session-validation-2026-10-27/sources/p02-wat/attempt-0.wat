(local $max i32)
local.get $a
local.get $b
local.get $a
local.get $b
i32.gt_s
select
local.set $max
local.get $max
local.get $c
local.get $max
local.get $c
i32.gt_s
select
