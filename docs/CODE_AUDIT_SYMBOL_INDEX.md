# 全项目代码符号与检查索引

范围为 Git 跟踪及未忽略的新文件快照。Python 列出顶层函数/类；TS/JS 列出顶层函数、类、类型及箭头函数。自动解析不是逐行人工审查；不列匿名回调、类方法、动态注册或运行时反射。实际人工审查范围和待核查部分见 CODE_AUDIT_REPORT.md。


## `backend/Functional/Functional.py`

`zlgCanFuncs:28`

## `backend/Functional/__init__.py`

无顶层命名符号

## `backend/app/__init__.py`

无顶层命名符号

## `backend/app/adb.py`

`AdbError:19`, `AdbDevice:24`, `FrameStats:34`, `get_sdk_version:55`, `run_adb:64`, `list_devices:110`, `enrich_device:135`, `_float:147`, `_int:154`, `parse_top:161`, `parse_cpuinfo:209`, `parse_proc_stat_cpu:222`, `merge_processes:236`, `parse_proc_meminfo:262`, `parse_meminfo:275`, `parse_surface_latency:335`, `parse_surface_latency_stats:365`, `_percentile:406`, `_fps_from_present_ns:418`, `parse_framestats:431`, `parse_frame_timeline:523`, `parse_gfxinfo_summary:601`, `list_surface_layers:619`, `extract_foreground_package:635`, `get_foreground_package:641`, `choose_surface_layer:647`

## `backend/app/can_service.py`

`CanService:22`, `Open:296`, `Send:301`, `Trigger:309`, `Pulse:311`, `Record:313`, `service:315`, `call:317`, `devices:321`, `status:325`, `open_device:327`, `close_device:330`, `send:333`, `trigger:336`, `pulse:344`, `stop_send:349`, `frames:352`, `signal_series:357`, `record_start:361`, `record_stop:365`, `DbcLoad:369`, `WakeProfile:371`, `WakeStart:373`, `browse_files:377`, `load_dbc:388`, `dbc_info:391`, `wake_profile:394`, `save_profile:397`, `start_wake:400`, `stop_wake:404`, `import_wake_profile:410`

## `backend/app/device_logs.py`

`DeviceLogConfig:32`, `_list_files:49`, `export_and_clean_device_logs:55`

## `backend/app/fps_probe.py`

`_probe_present:35`, `probe_status:71`

## `backend/app/frame_sources.py`

`FrameCapabilities:47`, `source_priority:61`, `pick_recommended_source:78`, `probe_frame_capabilities:86`, `capture_frame_metrics:141`

## `backend/app/interaction.py`

`InteractionError:36`, `InteractionRuntime:41`, `InteractionTraceManager:70`

## `backend/app/main.py`

`lifespan:33`, `manager_from:71`, `health:76`, `devices:85`, `surface_layers:95`, `frame_capabilities:103`, `start_session:113`, `fps_probe_page:130`, `fps_probe_status:136`, `list_sessions:145`, `import_session:151`, `active_session:170`, `stop_session:181`, `delete_session:193`, `session_detail:218`, `session_series:226`, `session_processes:240`, `session_events:253`, `session_report:261`, `export_session:285`, `interactions_from:294`, `start_interaction:299`, `interaction_detail:318`, `download_interaction_trace:326`

## `backend/app/models.py`

`MetricsConfig:11`, `StartSessionRequest:22`, `ProcessSample:61`, `SystemMemorySample:73`, `SamplePayload:83`

## `backend/app/monitor.py`

`RuntimeSession:45`, `MonitorManager:81`

## `backend/app/monkey.py`

`EventRatios:38`, `MonkeyRequest:59`, `remote_paths:101`, `monkey_arguments:108`, `runner_script:122`, `Runtime:171`, `MonkeyManager:191`, `manager:508`, `defaults:513`, `runs:520`, `start:526`, `detail:537`, `stop:550`, `delete_run:558`, `download:581`, `summary:605`, `report:618`

## `backend/app/monkey_records.py`

`MonkeyReportStore:19`, `connect:81`, `MonkeyRecords:93`

## `backend/app/record_cleanup.py`

`stage_directory:7`, `restore_directory:17`, `finish_directory:22`

## `backend/app/report.py`

`_fmt:86`, `_mi:92`, `_escape:96`, `_downsample:100`, `_svg_line_chart:112`, `_overview_card:164`, `_summary_metric:172`, `_build_app_uss_trends:176`, `generate_report:225`

## `backend/app/storage.py`

`ensure_system_memory_columns:21`, `SessionWriter:29`, `_connect:105`, `_column_exists:114`, `_calculate_summary:119`, `SessionStore:153`

## `backend/app/ui_element_capture.py`

`ElementNotFound:11`, `ElementNotUnique:15`, `capture_element:19`, `main:52`

## `backend/app/ui_images.py`

`png_size:21`, `UIImages:30`

## `backend/app/ui_map.py`

`uid:23`, `parse_tree:27`, `identity:63`, `observation_package:77`, `locator:83`, `resolve:94`, `route:106`, `Device:122`, `MapStore:196`, `Serial:336`, `ElementRequest:340`, `TapRequest:349`, `SwipeRequest:353`, `ExecuteRequest:361`, `EditRequest:367`, `store:373`, `guard:378`, `get_node:394`, `graph:401`, `observe:406`, `frame:414`, `current_elements:420`, `save:440`, `saved_observation:474`, `tap:482`, `edit:526`, `execute:562`, `swipe:655`, `AppSave:670`, `PageSave:675`, `MoveElement:683`, `save_app:690`, `save_page:705`, `move_element:794`, `ParentRead:817`, `parent_element:822`

## `backend/app/ui_navigation.py`

`NavigationStore:9`

## `backend/app/ui_stream.py`

`touch_packet:17`, `stream:26`

## `backend/app/ui_testing.py`

`ExploreScope:38`, `Selector:50`, `TapCoordinates:59`, `SwipeCoordinates:65`, `DelayOptions:74`, `ElementCheck:79`, `ImageRegion:84`, `ImageCheck:98`, `ScreenshotOptions:116`, `LoopOptions:129`, `VariableCheck:135`, `ScreenshotCheck:142`, `VariableAssignment:163`, `CanTarget:185`, `RawCanValue:192`, `CanSendOptions:197`, `Step:232`, `Parameter:287`, `TestCase:294`, `RunRequest:398`, `Settings:406`, `UIStore:423`, `draft_from_run:488`, `UIManager:549`, `manager:838`, `get_run:842`, `settings_get:850`, `settings_save:855`, `doctor:863`, `cases_list:878`, `ProjectName:882`, `ui_projects:887`, `create_ui_project:892`, `delete_ui_project:900`, `ui_project_overview:1014`, `ui_project_images:1062`, `save_ui_project_image:1070`, `ui_project_image:1089`, `ImageTarget:1103`, `image_deletion_details:1107`, `ui_image_references:1144`, `delete_ui_image:1154`, `upload_baseline:1195`, `case_save:1219`, `case_delete:1239`, `ExportRequest:1245`, `ScanRequest:1249`, `scan_start:1256`, `cases_export:1270`, `runs_list:1308`, `delete_ui_run:1315`, `run_start:1340`, `run_get:1348`, `run_events:1353`, `structured_report:1359`, `execution_draft:1367`, `step_metrics:1374`, `run_metrics:1398`, `run_cancel:1404`, `artifact:1410`

## `backend/can_adapters/__init__.py`

无顶层命名符号

## `backend/can_adapters/device_manager.py`

`CanDeviceManager:5`

## `backend/can_adapters/hirain.py`

`_sdk:14`, `_driver_channels:24`, `discover:41`, `HirainCanBackend:66`

## `backend/tests/test_low_overhead_capture.py`

`make_runtime:11`, `test_proc_stat_counts_iowait_as_idle_without_double_counting_guest:16`, `test_cpu_uses_deltas_and_falls_back_when_proc_is_unavailable:21`, `test_render_reuses_framestats_summary_and_falls_back:58`

## `backend/tests/test_monkey.py`

`config:20`, `test_invalid_configuration_rejected:30`, `test_command_preserves_zero_ratios_and_exclusive_list_mode:37`, `new_records:54`, `source_session:62`, `test_hourly_csv_is_complete_and_merge_is_idempotent:80`, `test_missing_samples_and_large_time_gaps_do_not_become_zeroes:112`, `fake_adb:155`, `managers:178`, `settled:185`, `running:191`, `test_multi_hour_run_rolls_exports_and_merges_without_reports:201`, `test_continuous_stop_preserves_partial_and_blocks_other_tasks:221`, `test_failure_paths_release_ownership_and_keep_records:248`, `test_shutdown_and_recovery_archive_without_resuming_inputs:261`, `test_combined_csv_download_for_legacy_run_and_running_guard:285`, `test_monkey_report_uses_merged_records_and_supports_png:307`, `test_log_and_tail_remain_bounded:337`, `test_cancelled_adb_command_reaps_host_subprocess:354`, `test_immediate_stop_before_task_entry_does_not_start_device_input:367`, `test_disk_guard_stops_input_and_preserves_partial:383`, `test_expired_memory_is_missing_and_cpu_memory_mode_skips_foreground_probe:400`, `test_monkey_records_filter_by_project_before_pagination:418`

## `backend/tests/test_parsers.py`

`test_parse_top_and_cpuinfo:10`, `test_parse_meminfo_sections:21`, `test_parse_surface_latency:43`, `test_merge_processes_combines_cpu_and_memory:50`, `_framestats_row:60`, `test_parse_framestats:71`, `test_parse_framestats_short_rows_older_android:105`, `test_parse_frame_timeline:120`, `test_parse_frame_timeline_samsung_oem:169`, `test_parse_surface_latency_stats:205`, `test_extract_foreground_package_android13_top_resumed:214`, `test_extract_foreground_package_legacy_m_resumed:222`, `test_extract_foreground_package_none:228`, `test_device_log_config_enabled_pairs:232`, `test_device_log_path_validation:240`, `test_device_log_folder_name_format:251`, `test_parse_gfxinfo_summary_single_process:260`, `test_parse_gfxinfo_summary_takes_most_active_process:267`, `test_parse_gfxinfo_summary_first_process_active:279`, `test_parse_gfxinfo_summary_skips_broken_section:291`, `test_parse_gfxinfo_summary_none:302`, `test_source_priority_gates_by_sdk:306`

## `backend/tests/test_project_association.py`

`test_start_endpoints_validate_project_and_persist_server_name:13`, `test_record_deletion_respects_active_tasks_and_cleans_files:43`

## `backend/tests/test_report.py`

`_make_report_session:16`, `test_report_contains_overview_cards:57`, `test_report_contains_charts:66`, `test_uss_links_point_to_initially_collapsed_charts:74`, `test_report_contains_processes_and_events:99`, `test_selected_metrics_filter_summary_charts_rankings_and_event_details:106`, `test_report_unknown_session_raises:118`

## `backend/tests/test_storage.py`

`_make_store:14`, `_create_session:19`, `test_session_dir_name_uses_device_and_date:38`, `test_session_dir_name_handles_network_serial_and_conflict:46`, `test_database_path_resolves_by_session_id:55`, `test_get_all_samples_is_not_limited_to_live_window:68`, `test_database_path_legacy_uuid_dir_still_works:94`, `test_import_database_uses_device_date_dir_and_conflict_new_id:115`, `test_list_sessions_includes_dir_name:129`, `test_projects_are_saved_filtered_and_legacy_databases_import:138`

## `backend/tests/test_system_memory.py`

`test_proc_meminfo_uses_available_without_fallback:34`, `session:43`, `test_new_rate_round_trips_through_database_summary_csv_and_report:64`, `test_legacy_records_leave_system_memory_unrecorded:85`, `test_monkey_merges_old_and_new_segments_without_inventing_history:96`, `test_system_memory_does_not_wait_for_slow_pss_and_expires:124`

## `backend/tests/test_ui_map.py`

`tree:9`, `setup:15`, `save_app_page:33`, `test_switch_state_not_page_identity_and_xpath_unique:39`, `test_graph_does_not_use_unverified_or_reverse_edges:51`, `test_saved_element_persists_and_rename_keeps_locator:59`, `test_stale_snapshot_cannot_click:77`, `test_set_checked_is_idempotent_and_duplicate_request_rejected:86`, `test_recording_switch_rejected_before_input:99`, `entrance:107`, `test_record_verify_replay_and_stop_on_wrong_destination:112`, `test_busy_device_rejected_and_stale_swipe_sends_nothing:134`, `test_operation_does_not_change_saved_tree:149`, `test_explicit_page_save_commits_temporary_entry_and_replays:160`, `test_package_launch_recovers_disconnected_page_and_migrates_old_map:177`, `test_package_launch_unknown_landing_stops_without_click:192`, `test_launch_uses_resolved_launcher_and_rejects_missing_entry:202`, `test_save_requires_page_and_rejects_wrong_owner:220`, `test_move_and_undo_preserve_locator_and_cross_app_requires_recapture:229`, `test_page_identity_ignores_dynamic_values_not_titles_tabs_or_dialogs:251`, `test_missing_path_is_explicit_and_page_cycle_rejected:264`, `test_scrcpy_touch_packet_bounds:277`, `test_legacy_migration_keeps_ids_and_only_recovers_proven_dynamic_failure:283`, `test_parent_element_promotion_preserves_complete_entry_evidence:305`, `test_legacy_parent_backfill_uses_source_not_destination:330`, `test_parent_entry_can_launch_another_package:342`

## `backend/tests/test_ui_navigation.py`

`test_navigation_is_device_scoped_and_does_not_edit_tree:5`, `test_oversized_or_malformed_update_preserves_knowledge:16`

## `backend/tests/test_ui_testing.py`

`case:14`, `test_rejects_duplicate_steps_and_secret_defaults:23`, `test_store_recovers_all_unfinished_runs:32`, `test_settings_never_return_key_and_preserve_it_on_update:41`, `setup_worker:52`, `test_real_worker_snapshot_secret_omission_and_device_lock:62`, `test_cancel_and_abnormal_exit_have_terminal_state:87`, `can_step:108`, `test_can_steps_use_selected_dbc_and_share_the_bus:117`, `test_cancel_can_loop_keeps_unrelated_sends:171`, `test_log_export_preserves_device_files_when_ui_overlaps:209`, `test_draft_uses_completed_semantic_actions_and_keeps_unresolved_steps:223`, `test_case_requires_explicit_exploration_scope:239`, `test_step_metrics_only_include_samples_inside_step_window:244`, `test_exploration_draft_contains_initial_and_restored_state_assertions:259`, `test_scan_rejects_switches_and_model_assertions:268`, `test_selector_cannot_silently_fall_back_to_model:276`, `test_new_step_schema_rejects_missing_coordinates_and_empty_selector:283`, `test_image_baseline_is_local_and_included_in_case_export:293`, `test_project_images_are_scoped_and_export_with_cases:318`, `test_image_assertion_modes_validate_regions_and_preserve_legacy_default:377`, `test_screenshot_comparison_references_prior_steps_and_survives_draft_creation:390`, `test_control_flow_validates_branch_scopes_and_remaps_drafts:410`, `test_named_variable_assignments_validate_sources_and_remap_screenshot_ids:446`, `test_uiautomator2_element_capture_uses_exact_unique_bounds:464`, `test_project_overview_uses_all_cases_and_run_snapshots:493`, `test_delete_project_removes_owned_data_and_preserves_shared_images:522`, `test_overwriting_named_image_preserves_references_and_coordinates:568`, `test_overwrite_rollback_restores_all_existing_image_files:609`, `test_delete_image_warns_references_and_preserves_other_project:640`, `test_delete_legacy_image_reports_case_references:703`, `test_project_overview_and_deletion_include_performance_and_monkey:725`

## `backend/tests/zcan/test_can_bench.py`

`BenchTest:8`

## `backend/tests/zcan/test_can_service_lifecycle.py`

`LifecycleTest:10`

## `backend/tests/zcan/test_catalog.py`

`CatalogTests:8`

## `backend/tests/zcan/test_hirain.py`

`FakeBus:8`, `sdk:20`, `HirainTest:27`

## `backend/tests/zcan/test_integration.py`

`IntegrationTests:11`

## `backend/tests/zcan/test_macos_usb.py`

`MacUsbWireTests:18`

## `backend/tests/zcan/test_periodic.py`

`model_intervals:24`, `DeadlineTests:47`, `PeriodicIntegrationTests:86`, `WindowsLayoutAndTimingTests:234`

## `backend/tests/zcan/test_signal_series.py`

`SignalSeriesTests:9`

## `backend/tools/can_bench.py`

`stats:21`, `run:29`, `main:134`

## `backend/tools/can_discover.py`

无顶层命名符号

## `backend/zlg/FunctionRely.py`

`FunctionRely:6`

## `backend/zlg/__init__.py`

无顶层命名符号

## `backend/zlg/backend.py`

`CanBackend:11`, `validate_channel:26`, `LoopbackBackend:31`

## `backend/zlg/logutil.py`

`Logs:8`

## `backend/zlg/macos_usb.py`

`_tx_packet:31`, `_periodic_payload:41`, `_RxDecoder:52`, `MacUsbCanBackend:118`

## `backend/zlg/native.py`

`load_vendor_library:13`, `configure_zcan:35`

## `backend/zlg/paths.py`

`resource_path:9`, `data_path:13`

## `backend/zlg/periodic.py`

`HardwarePeriodicSend:14`, `Deadline:35`, `PeriodicSend:51`, `PulseSend:137`

## `backend/zlg/tx_timing.py`

`TxSample:15`, `TxTiming:43`

## `backend/zlg/ucfd_identity.py`

`crc16:17`, `query_version:26`, `query_identity:37`, `encode_packet:44`, `exchange_command:49`

## `backend/zlg/wake.py`

`parse_signal_number:8`, `resolve_signal_value:21`, `encode_wake_message:47`

## `backend/zlg/zlgcan.py`

`ZCAN_DEVICE_INFO:124`, `_ZCAN_CHANNEL_CAN_INIT_CONFIG:202`, `_ZCAN_CHANNEL_CANFD_INIT_CONFIG:212`, `_ZCAN_CHANNEL_INIT_CONFIG:223`, `ZCAN_CHANNEL_INIT_CONFIG:226`, `ZCAN_CHANNEL_ERR_INFO:231`, `ZCAN_CHANNEL_STATUS:237`, `ZCAN_CAN_FRAME:249`, `ZCAN_CANFD_FRAME:261`, `ZCANdataFlag:276`, `ZCANFDData:305`, `ZCANDataObj:320`, `ZCAN_Transmit_Data:335`, `ZCAN_Receive_Data:339`, `ZCAN_TransmitFD_Data:343`, `ZCAN_ReceiveFD_Data:348`, `ZCAN_AUTO_TRANSMIT_OBJ:352`, `ZCANFD_AUTO_TRANSMIT_OBJ:359`, `ZCANFD_AUTO_TRANSMIT_OBJ_PARAM:366`, `IProperty:372`, `ZCAN:378`, `DBC:682`, `WindowsCanBackend:819`, `backend_mode:998`, `open_backend:1005`, `zlgCan:1021`

## `client/public/__manus__/debug-collector.js`

无顶层命名符号

## `client/src/App.tsx`

`SidebarLink:27`, `WorkspaceNav:60`, `Router:224`, `App:238`

## `client/src/components/CanSignalTrend.tsx`

`formatTime:23`, `Message:25`, `Point:30`, `Series:31`, `SignalSelection:32`, `SignalPlot:34`, `CanSignalTrend:183`

## `client/src/components/DeleteRecordButton.tsx`

`DeleteRecordButton:16`

## `client/src/components/ErrorBoundary.tsx`

`Props:5`, `State:9`, `ErrorBoundary:14`

## `client/src/components/InteractionDiagnostic.tsx`

`InteractionRecord:7`, `api:23`, `InteractionDiagnostic:35`

## `client/src/components/ManusDialog.tsx`

`ManusDialogProps:12`, `ManusDialog:21`

## `client/src/components/Map.tsx`

`loadMapScript:95`, `MapViewProps:112`, `MapView:119`

## `client/src/components/PageMapWorkbench.tsx`

`App:29`, `Page:30`, `Element:48`, `Edge:59`, `Graph:66`, `Pick:74`, `Region:75`, `BaselineDraft:76`, `Menu:81`, `api:96`, `PageMapWorkbench:115`

## `client/src/components/PageStructure.tsx`

`request:6`, `Node:22`, `Tree:36`, `Scan:37`, `Event:38`, `running:53`, `Control:64`, `PageStructure:88`

## `client/src/components/PerformanceReportDialog.tsx`

`Metric:23`, `Source:24`, `PerformanceReportDialog:26`

## `client/src/components/ScrcpyScreen.tsx`

`ScrcpyHandle:9`

## `client/src/components/UiCanStepFields.tsx`

`CanSearchSelect:24`, `Props:103`, `UiCanStepFields:110`

## `client/src/components/UiCaseLibrary.tsx`

`LibraryCase:14`, `Props:24`, `optionsText:97`, `UiCaseLibrary:112`

## `client/src/components/UiDevicePreview.tsx`

`UiDevicePreviewHandle:22`, `Frame:23`, `Pixel:24`, `Props:25`, `readDevice:35`, `selectorFor:54`, `ElementDetail:79`, `pixelText:108`, `PixelMarker:110`

## `client/src/components/UiImageLibrary.tsx`

`UiProjectOptions:23`, `UiImageLibrary:35`

## `client/src/components/UiProjectManagement.tsx`

`Overview:18`, `UiProjectManagement:50`

## `client/src/components/UiStepFields.tsx`

`Props:27`, `UiStepFields:43`

## `client/src/components/ui/accordion.tsx`

`Accordion:7`, `AccordionItem:13`, `AccordionTrigger:26`, `AccordionContent:48`

## `client/src/components/ui/alert-dialog.tsx`

`AlertDialog:7`, `AlertDialogTrigger:13`, `AlertDialogPortal:21`, `AlertDialogOverlay:29`, `AlertDialogContent:45`, `AlertDialogHeader:64`, `AlertDialogFooter:77`, `AlertDialogTitle:93`, `AlertDialogDescription:106`, `AlertDialogAction:119`, `AlertDialogCancel:131`

## `client/src/components/ui/alert.tsx`

`Alert:22`, `AlertTitle:37`, `AlertDescription:50`

## `client/src/components/ui/aspect-ratio.tsx`

`AspectRatio:3`

## `client/src/components/ui/avatar.tsx`

`Avatar:6`, `AvatarImage:22`, `AvatarFallback:35`

## `client/src/components/ui/badge.tsx`

`Badge:28`

## `client/src/components/ui/breadcrumb.tsx`

`Breadcrumb:7`, `BreadcrumbList:11`, `BreadcrumbItem:24`, `BreadcrumbLink:34`, `BreadcrumbPage:52`, `BreadcrumbSeparator:65`, `BreadcrumbEllipsis:83`

## `client/src/components/ui/button-group.tsx`

`ButtonGroup:24`, `ButtonGroupText:40`, `ButtonGroupSeparator:60`

## `client/src/components/ui/button.tsx`

`Button:39`

## `client/src/components/ui/calendar.tsx`

`Calendar:12`, `CalendarDayButton:173`

## `client/src/components/ui/card.tsx`

`Card:5`, `CardHeader:18`, `CardTitle:31`, `CardDescription:41`, `CardAction:51`, `CardContent:64`, `CardFooter:74`

## `client/src/components/ui/carousel.tsx`

`CarouselApi:10`, `UseCarouselParameters:11`, `CarouselOptions:12`, `CarouselPlugin:13`, `CarouselProps:15`, `CarouselContextProps:22`, `useCarousel:33`, `Carousel:43`, `CarouselContent:133`, `CarouselItem:154`, `CarouselPrevious:172`, `CarouselNext:202`

## `client/src/components/ui/chart.tsx`

`ChartConfig:9`, `ChartContextProps:19`, `useChart:25`, `ChartContainer:35`, `ChartStyle:70`, `ChartTooltipContent:105`, `ChartLegendContent:253`, `getPayloadConfigFromPayload:310`

## `client/src/components/ui/checkbox.tsx`

`Checkbox:7`

## `client/src/components/ui/collapsible.tsx`

`Collapsible:3`, `CollapsibleTrigger:9`, `CollapsibleContent:20`

## `client/src/components/ui/command.tsx`

`Command:16`, `CommandDialog:32`, `CommandInput:63`, `CommandList:85`, `CommandEmpty:101`, `CommandGroup:113`, `CommandSeparator:129`, `CommandItem:142`, `CommandShortcut:158`

## `client/src/components/ui/context-menu.tsx`

`ContextMenu:7`, `ContextMenuTrigger:13`, `ContextMenuGroup:21`, `ContextMenuPortal:29`, `ContextMenuSub:37`, `ContextMenuRadioGroup:43`, `ContextMenuSubTrigger:54`, `ContextMenuSubContent:78`, `ContextMenuContent:94`, `ContextMenuItem:112`, `ContextMenuCheckboxItem:135`, `ContextMenuRadioItem:161`, `ContextMenuLabel:185`, `ContextMenuSeparator:205`, `ContextMenuShortcut:218`

## `client/src/components/ui/dialog.tsx`

`useDialogComposition:19`, `Dialog:22`, `DialogTrigger:56`, `DialogPortal:62`, `DialogClose:68`, `DialogOverlay:74`, `DialogContent:92`, `DialogHeader:148`, `DialogFooter:158`, `DialogTitle:171`, `DialogDescription:184`

## `client/src/components/ui/drawer.tsx`

`Drawer:6`, `DrawerTrigger:12`, `DrawerPortal:18`, `DrawerClose:24`, `DrawerOverlay:30`, `DrawerContent:46`, `DrawerHeader:73`, `DrawerFooter:86`, `DrawerTitle:96`, `DrawerDescription:109`

## `client/src/components/ui/dropdown-menu.tsx`

`DropdownMenu:7`, `DropdownMenuPortal:13`, `DropdownMenuTrigger:21`, `DropdownMenuContent:32`, `DropdownMenuGroup:52`, `DropdownMenuItem:60`, `DropdownMenuCheckboxItem:83`, `DropdownMenuRadioGroup:109`, `DropdownMenuRadioItem:120`, `DropdownMenuLabel:144`, `DropdownMenuSeparator:164`, `DropdownMenuShortcut:177`, `DropdownMenuSub:193`, `DropdownMenuSubTrigger:199`, `DropdownMenuSubContent:223`

## `client/src/components/ui/empty.tsx`

`Empty:5`, `EmptyHeader:18`, `EmptyMedia:46`, `EmptyTitle:61`, `EmptyDescription:71`, `EmptyContent:84`

## `client/src/components/ui/field.tsx`

`FieldSet:8`, `FieldLegend:22`, `FieldGroup:42`, `Field:79`, `FieldContent:95`, `FieldLabel:108`, `FieldTitle:126`, `FieldDescription:139`, `FieldSeparator:154`, `FieldError:184`

## `client/src/components/ui/form.tsx`

`FormFieldContextValue:21`, `FormField:32`, `useFormField:45`, `FormItemContextValue:68`, `FormItem:76`, `FormLabel:90`, `FormControl:107`, `FormDescription:126`, `FormMessage:139`

## `client/src/components/ui/hover-card.tsx`

`HoverCard:6`, `HoverCardTrigger:12`, `HoverCardContent:20`

## `client/src/components/ui/input-group.tsx`

`InputGroup:9`, `InputGroupAddon:58`, `InputGroupButton:98`, `InputGroupText:117`, `InputGroupInput:129`, `InputGroupTextarea:145`

## `client/src/components/ui/input-otp.tsx`

`InputOTP:7`, `InputOTPGroup:27`, `InputOTPSlot:37`, `InputOTPSeparator:67`

## `client/src/components/ui/input.tsx`

`Input:6`

## `client/src/components/ui/item.tsx`

`ItemGroup:8`, `ItemSeparator:19`, `Item:54`, `ItemMedia:91`, `ItemContent:106`, `ItemTitle:119`, `ItemDescription:132`, `ItemActions:146`, `ItemHeader:156`, `ItemFooter:169`

## `client/src/components/ui/kbd.tsx`

`Kbd:3`, `KbdGroup:18`

## `client/src/components/ui/label.tsx`

`Label:6`

## `client/src/components/ui/menubar.tsx`

`Menubar:7`, `MenubarMenu:23`, `MenubarGroup:29`, `MenubarPortal:35`, `MenubarRadioGroup:41`, `MenubarTrigger:49`, `MenubarContent:65`, `MenubarItem:89`, `MenubarCheckboxItem:112`, `MenubarRadioItem:138`, `MenubarLabel:162`, `MenubarSeparator:182`, `MenubarShortcut:195`, `MenubarSub:211`, `MenubarSubTrigger:217`, `MenubarSubContent:241`

## `client/src/components/ui/navigation-menu.tsx`

`NavigationMenu:8`, `NavigationMenuList:32`, `NavigationMenuItem:48`, `NavigationMenuTrigger:65`, `NavigationMenuContent:85`, `NavigationMenuViewport:102`, `NavigationMenuLink:124`, `NavigationMenuIndicator:140`

## `client/src/components/ui/pagination.tsx`

`Pagination:11`, `PaginationContent:23`, `PaginationItem:36`, `PaginationLinkProps:40`, `PaginationLink:45`, `PaginationPrevious:68`, `PaginationNext:85`, `PaginationEllipsis:102`

## `client/src/components/ui/popover.tsx`

`Popover:6`, `PopoverTrigger:12`, `PopoverContent:18`, `PopoverAnchor:40`

## `client/src/components/ui/progress.tsx`

`Progress:6`

## `client/src/components/ui/radio-group.tsx`

`RadioGroup:7`, `RadioGroupItem:20`

## `client/src/components/ui/resizable.tsx`

`ResizablePanelGroup:7`, `ResizablePanel:23`, `ResizableHandle:29`

## `client/src/components/ui/scroll-area.tsx`

`ScrollArea:6`, `ScrollBar:29`

## `client/src/components/ui/select.tsx`

`Select:7`, `SelectGroup:13`, `SelectValue:19`, `SelectTrigger:25`, `SelectContent:51`, `SelectLabel:88`, `SelectItem:101`, `SelectSeparator:125`, `SelectScrollUpButton:138`, `SelectScrollDownButton:156`

## `client/src/components/ui/separator.tsx`

`Separator:6`

## `client/src/components/ui/sheet.tsx`

`Sheet:9`, `SheetTrigger:13`, `SheetClose:19`, `SheetPortal:25`, `SheetOverlay:31`, `SheetContent:47`, `SheetHeader:84`, `SheetFooter:94`, `SheetTitle:104`, `SheetDescription:117`

## `client/src/components/ui/sidebar.tsx`

`SidebarContextProps:34`, `useSidebar:46`, `SidebarProvider:55`, `Sidebar:153`, `SidebarTrigger:263`, `SidebarRail:289`, `SidebarInset:314`, `SidebarInput:328`, `SidebarHeader:342`, `SidebarFooter:353`, `SidebarSeparator:364`, `SidebarContent:378`, `SidebarGroup:392`, `SidebarGroupLabel:403`, `SidebarGroupAction:424`, `SidebarGroupContent:447`, `SidebarMenu:461`, `SidebarMenuItem:472`, `SidebarMenuButton:505`, `SidebarMenuAction:555`, `SidebarMenuBadge:587`, `SidebarMenuSkeleton:609`, `SidebarMenuSub:647`, `SidebarMenuSubItem:662`, `SidebarMenuSubButton:676`

## `client/src/components/ui/skeleton.tsx`

`Skeleton:3`

## `client/src/components/ui/slider.tsx`

`Slider:6`

## `client/src/components/ui/sonner.tsx`

`Toaster:4`

## `client/src/components/ui/spinner.tsx`

`Spinner:5`

## `client/src/components/ui/switch.tsx`

`Switch:6`

## `client/src/components/ui/table.tsx`

`Table:5`, `TableHeader:20`, `TableBody:30`, `TableFooter:40`, `TableRow:53`, `TableHead:66`, `TableCell:79`, `TableCaption:92`

## `client/src/components/ui/tabs.tsx`

`Tabs:6`, `TabsList:19`, `TabsTrigger:35`, `TabsContent:51`

## `client/src/components/ui/textarea.tsx`

`Textarea:6`

## `client/src/components/ui/toggle-group.tsx`

`ToggleGroup:17`, `ToggleGroupItem:43`

## `client/src/components/ui/toggle.tsx`

`Toggle:29`

## `client/src/components/ui/tooltip.tsx`

`TooltipProvider:6`, `Tooltip:19`, `TooltipTrigger:29`, `TooltipContent:35`

## `client/src/const.ts`

`getLoginUrl:4`

## `client/src/contexts/ThemeContext.tsx`

`Theme:3`, `ThemeContextType:5`, `ThemeProviderProps:13`, `ThemeProvider:19`, `useTheme:58`

## `client/src/hooks/useComposition.ts`

`UseCompositionReturn:4`, `UseCompositionOptions:13`, `TimerResponse:21`, `useComposition:23`

## `client/src/hooks/useMobile.tsx`

`useIsMobile:5`

## `client/src/hooks/usePersistFn.ts`

`noop:3`, `usePersistFn:8`

## `client/src/lib/backend.ts`

无顶层命名符号

## `client/src/lib/canCatalog.ts`

`CanValue:1`, `CanSignal:3`, `CanMessage:15`, `CanCatalog:25`

## `client/src/lib/canNavigation.ts`

`CanSection:19`, `getCanSection:21`

## `client/src/lib/uiImages.ts`

`UiProject:4`, `UiImage:5`, `uiImagesPath:15`, `uiImageUrl:19`

## `client/src/lib/uiNavigation.ts`

`getUiSection:19`

## `client/src/lib/uiObservation.ts`

`MapNode:1`, `Observation:18`, `screenPixel:32`, `elementCenter:53`, `nodesAtPoint:60`

## `client/src/lib/uiSteps.ts`

`StepType:23`, `isAssertion:52`, `isCapture:55`, `isBlock:57`, `isCanStep:59`, `Selector:64`, `StepOptions:72`, `ExploreScope:110`, `Step:121`, `stepLayout:138`, `expandElseBlocks:170`, `StepDrop:186`, `childInsertion:188`, `relocateStep:205`, `changeStepType:256`

## `client/src/lib/utils.ts`

`cn:4`

## `client/src/lib/workspaceProjects.ts`

`currentProjectId:6`, `rememberProject:13`, `useWorkspaceProjects:21`

## `client/src/main.tsx`

无顶层命名符号

## `client/src/pages/CanBus.tsx`

`Draft:16`, `Profile:23`, `Task:24`, `Status:31`, `Frame:40`, `CanDevice:48`, `Discovery:49`, `Directory:50`, `displaySignal:56`, `CanBus:68`

## `client/src/pages/Home.tsx`

`MetricKey:49`, `ProcessMetric:50`, `Device:52`, `Health:61`, `SessionPoint:69`, `FrameCapabilities:92`, `frameSourceLabel:113`, `SummaryMetric:117`, `MonitorSession:124`, `ProcessRow:141`, `MonitorEvent:149`, `loadJsonStorage:163`, `SectionHeader:176`, `notifyLogExport:203`, `Countdown:219`, `requestApi:242`, `formatTime:254`, `formatDateTime:258`, `stateLabel:262`, `stateTone:266`, `formatValue:273`, `displayMetricValue:278`, `formatProcessValue:282`, `formatMemorySummary:288`, `metricSummary:292`, `StatusPill:296`, `MetricToggle:306`, `MetricChart:323`, `Home:401`

## `client/src/pages/MonkeyTesting.tsx`

`Ratios:9`, `Config:10`, `Segment:24`, `Run:33`, `Metric:50`, `Summary:62`, `Device:74`, `busy:124`, `fmt:126`, `clock:128`, `date:132`, `api:134`, `restored:163`, `MonkeyTesting:184`

## `client/src/pages/NotFound.tsx`

`NotFound:6`

## `client/src/pages/UITesting.tsx`

`Parameter:76`, `Case:77`, `Run:88`, `Event:101`, `Config:116`, `StepMetric:125`, `active:146`, `uid:147`, `newStep:148`, `newElse:157`, `stepLimitReached:162`, `blank:164`, `sameScope:178`, `api:182`, `UITesting:203`

## `run-local.sh`

`shell 文件，未做 AST 符号解析`

## `server/index.ts`

`startServer:9`

## `shared/const.ts`

无顶层命名符号

## `tools/setup-scrcpy.sh`

`shell 文件，未做 AST 符号解析`

## `tools/ui-test/config.mjs`

`loadConfig:4`, `publicConfig:12`

## `tools/ui-test/doctor.mjs`

无顶层命名符号

## `tools/ui-test/element-capture.mjs`

`captureElement:10`

## `tools/ui-test/explore.mjs`

`exploreTree:7`

## `tools/ui-test/explore.test.mjs`

`xml:5`, `navigation:8`

## `tools/ui-test/framework.mjs`

`runCase:4`

## `tools/ui-test/framework.test.mjs`

`step:5`

## `tools/ui-test/hierarchy.mjs`

`list:6`, `parseHierarchy:9`, `selectorFor:106`, `matchingNodes:114`, `resolveSelector:122`, `createTreeDevice:134`

## `tools/ui-test/hierarchy.test.mjs`

`node:4`

## `tools/ui-test/image-assertion.mjs`

`decode:3`, `searchScreenshot:11`, `cropScreenshot:85`, `compareScreenshots:99`

## `tools/ui-test/inspect-tree.mjs`

无顶层命名符号

## `tools/ui-test/live-check.mjs`

`api:8`, `step:14`

## `tools/ui-test/navigation.mjs`

`navigationTarget:5`, `pageKey:19`, `findRoute:48`, `pointSelector:62`, `NavigationMemory:90`, `attachNavigationObserver:277`

## `tools/ui-test/navigation.test.mjs`

`tree:9`, `setup:32`

## `tools/ui-test/protocol.mjs`

`isCanStep:31`, `stepDeadlineMs:33`, `isAssertion:35`, `isCapture:43`, `requiresDevice:45`, `requiresModel:55`, `validInteger:62`, `captureSourceIds:76`, `validRegion:86`, `validateOptions:97`, `buildStepPlan:262`, `validateJob:317`, `substitute:525`, `scalarText:534`, `executeStep:542`

## `tools/ui-test/protocol.test.mjs`

`job:15`

## `tools/ui-test/runner.mjs`

`emit:19`, `finish:39`

## `tools/windows/build.py`

`sha256:41`, `download:49`, `command:71`, `extract:79`, `audit_bundle:94`, `build:129`

## `tools/windows/launcher.py`

`bundle_root:20`, `InstanceLock:24`, `instance_id:48`, `bind_socket:52`, `create_app:66`, `existing_url:97`, `prepare_environment:113`, `check_runtime:126`, `run:151`, `main:208`

## `tools/windows/smoke.py`

`fetch:27`, `smoke:32`

## `vite.config.ts`

`LogSource:19`, `ensureLogDir:21`, `trimLogFile:27`, `writeToLogFile:52`, `vitePluginManusDebugCollector:77`, `vitePluginStorageProxy:153`
