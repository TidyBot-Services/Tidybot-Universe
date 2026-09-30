# Five-seed v2.1 chain lock review (101–105)

Approval status: seed 101 retains the earlier exact package approval; seeds 102–105 are pending separate operator approval. No new simulator case has run.

The policy source, generation receipt, autonomous attention selection, budget, service revisions, runtime identities, cameras, safety limits, and all other settings are byte-identical to the approved seed-101 package values. Only seed, scene ID, object-set ID, and RoboCasa task prompt vary. Inputs are frozen pre-existing task metadata; their SHA-256 values are in `chain_lock_manifest.json`.

| Suite | Seed | Config SHA-256 | M1 lock file SHA-256 | M1 canonical identity SHA-256 |
| --- | ---: | --- | --- | --- |
| robosuite | 101 | `31ff175cfea8fb7e07097bb78f8293c457b62270ebc5143f3719b3a10adae98a` | `313e76d28e32cd5a86d80f39a75dde7aa2de1c0824a6bce223a70e3646df498b` | `f048067ab23be59eb78dd058cd876074f2e5efaaa960bed7157b15dc14311102` |
| robosuite | 102 | `484878c18e5a8956260982d05ef8eeab6ab46da075f7c68b3acbecade85c242d` | `45a9b7757a43857953c0a30e0dc30dc4eff16aaf3007d92d1e74ba7bb8f37a40` | `94e6671bb790aa0ee85ed45ba575b92a75549c2847daddf59b4aeef0c9c42148` |
| robosuite | 103 | `1a0953f5c5311e3e69f2ddee99781e0f912b6add87847e0b8c5321c320e7ff1d` | `6a8b830028e6922367d2d0dee5b18c1c926bccc7a04d74087d4aaeefdc99aed5` | `6fa8cb43886959e50154be90784015f71412ea34f54ea5289143866bb54cd623` |
| robosuite | 104 | `908524bba2e6cf4b3e0e7bea1a6a1afcc2b95c9585bd9451d3a7609787000fd0` | `4ffbbb691374bd497ac2bbb5cddf729d2a1cc94e0d82bb3438394813de005b44` | `d17d8e2329550a7bcc5c3208a81ebe0e30b8d4413f6f45bc98e6524d3b1f28e1` |
| robosuite | 105 | `17328bd0975ae1b932db494d118a249bd9b965afebaebd26042e39258713353b` | `b00440c441db16349bfbf93c86fca7571c19a0da37878dce1286706c09b43324` | `3cfad5b290813d4a197d7868aa5f589bf2eddaa5ba05ede1e63bd6c7d712266d` |
| robocasa | 101 | `fd32f0dfde74c6a754edf8e0b4ba78f94f733762e226bec2af177341edb2cc27` | `fc66f6d7a4d2319e9086af0963455eed42854aad845b1db50669254b8a2a194a` | `e264546053e2d2c0ecdc86f8e4e233cbc801885a8991a8b30835a0e053524000` |
| robocasa | 102 | `e56b73faea946fe2f9acfea378b3253c06cd5a9a13749480e64679354fa4cd4a` | `afacad91d26a1d020c9b8f632765cd24bff69ad60c4eb1f735a9d9074da380dd` | `60c67fe4b7c7ff8e0bc75f0ab0129b310b03975ab0cb6f120b33baa599c989bb` |
| robocasa | 103 | `a0358fc37b4632ebc83dc6df2cffdd4255b0fe034427b6299eeaa7f68e2166d3` | `d1972ba9163854a1684c0b57bde1e119b908ab233f6bf686b3539dca8b87d470` | `76d96ed935f5f126e829aa2ebd2c82e3645943cf2675ec2fac2c4008a2685dd6` |
| robocasa | 104 | `abe89f86bab4e179a2faf7ecbf56bf9f4f0ecfc0535679babf4332d87e7df384` | `94b06a9d8627b6eb0c5a7fa7a872ce5bc3fd21bc8c07acc0af871a1e45394f12` | `380c2aa76545348e490e0c14d7d75fcf3b24eeda7128ad341cfd8dcd85fef0e3` |
| robocasa | 105 | `77f5cdc5b07d156ac66583a8c5c787326aae3fe11a019e7bf2ef3c8ca64b071b` | `6a2eae567d1e1fe1148186450486567c347631f99010ad8a81a42edd1ed48119` | `9de1a95add9c64c6e750e9d3155f13696d32ff69a8292235aa44b86c2a1cabe1` |

The SHA-256 index covers every new config and M1 lock plus the manifest, this review, and validation report. The previously approved seed-101 files are referenced by exact SHA in the manifest, without changing their bytes. M1 locks use `autonomous`, four attempts, one available Advisor credit, 4096 tokens, and a 300-second wall clock, as in the approved seed-101 entry.

The RoboCasa task prompts are boxed drink, cup, mango, onion, and rolling pin for seeds 101–105 respectively. The policy has this vocabulary already; it was not changed for these seeds. Scene and object IDs were copied from recorded variation attestations. A mismatch at reset must stop the case; it must not be repaired after an outcome is seen.

Approval request: approve the exact SHA-256 of this new index for the eight seed 102–105 config files and eight matching M1 locks. After approval, run at most one development chain case per task and seed, in protocol order, stopping immediately on unsafe, unknown action, missing artifact, or version drift. Native task failure with intact evidence is recorded as failure, not a reason to swap policy. No profile, held-out, or seven-condition run is in this lock. All runs remain `formal_eligible=false`.
