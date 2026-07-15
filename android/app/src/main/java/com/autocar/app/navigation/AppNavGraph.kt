package com.autocar.app.navigation

import android.content.res.Configuration
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Gamepad
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.components.FaceViewport
import com.autocar.app.ui.components.GamepadControlsButton
import com.autocar.app.ui.components.GamepadNavEffect
import com.autocar.app.ui.components.LidarPolarPlot
import com.autocar.app.ui.components.MjpegView
import com.autocar.app.ui.components.ColorViewport
import com.autocar.app.ui.components.ObjectTrackViewport
import com.autocar.app.ui.components.SensorViewport
import com.autocar.app.ui.components.SidePanel
import com.autocar.app.ui.screens.DashboardScreen
import com.autocar.app.ui.screens.DriveScreen
import com.autocar.app.ui.screens.PathsScreen
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.LocalUiScale
import com.autocar.app.ui.theme.Rajdhani
import com.autocar.app.ui.theme.SurfaceDark
import kotlinx.coroutines.delay
import com.autocar.app.viewmodel.DriveViewModel
import com.autocar.app.viewmodel.FaceViewModel
import com.autocar.app.viewmodel.PathsViewModel
import com.autocar.app.viewmodel.SensorsViewModel
import com.autocar.app.viewmodel.SettingsViewModel
import com.autocar.app.viewmodel.ColorViewModel
import com.autocar.app.viewmodel.TrackViewModel

enum class NavTab(val label: String, val icon: ImageVector) {
    Sensors("Sensors", Icons.Default.Sensors),
    Face("Face", Icons.Default.Face),
    Track("Track", Icons.Default.CenterFocusStrong),
    Color("Color", Icons.Default.Palette),
    Dashboard("Dashboard", Icons.Default.Dashboard),
    Drive("Drive", Icons.Default.Gamepad),
    Paths("Paths", Icons.Default.Route),
}

/** Viewport tabs shown in the left rail (tablet landscape). */
private enum class Viewport(val label: String, val icon: ImageVector) {
    Sensors("Sensors", Icons.Default.Sensors),
    Face("Face", Icons.Default.Face),
    Track("Track", Icons.Default.CenterFocusStrong),
    Color("Color", Icons.Default.Palette),
}

/** Tabs shown in the right rail (tablet landscape) — side-panel content. */
private val railTabs = listOf(NavTab.Dashboard, NavTab.Drive, NavTab.Paths)

@Composable
fun AppNavGraph(
    gamepadManager: GamepadManager,
    scaleLevel: Int = 0,
    onScaleCycle: () -> Unit = {},
) {
    val config = LocalConfiguration.current
    val useRail = config.screenWidthDp >= 600 &&
        config.orientation == Configuration.ORIENTATION_LANDSCAPE
    val settingsVm: SettingsViewModel = viewModel()
    val layoutMode by settingsVm.layoutMode.collectAsState()

    // Hoisted state for gamepad navigation
    var selectedTab by remember { mutableStateOf(NavTab.Sensors) }
    var faceBackend by remember { mutableStateOf("mediapipe") }
    var faceMode by remember { mutableStateOf("trace") }
    var trackBackend by remember { mutableStateOf("orb") }
    var trackMode by remember { mutableStateOf("trace") }
    var colorMode by remember { mutableStateOf("trace") }
    var pathsSelectedIndex by remember { mutableIntStateOf(0) }

    // Grid layout state (hoisted for gamepad access)
    var gridExpandedCell by remember { mutableStateOf<GridCell?>(null) }
    var gridPanelTab by remember { mutableIntStateOf(0) }

    // Classic tablet layout state (hoisted for gamepad access)
    var classicActiveTab by remember { mutableStateOf<NavTab?>(null) }

    // Auto-hide chrome at scale level 4+ (index 3 = 1.75x)
    var chromeVisible by remember { mutableStateOf(true) }
    var lastButtonPress by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(lastButtonPress) {
        if (scaleLevel >= 3) { // level 4 = index 3 (1.75x)
            chromeVisible = true
            delay(3000)
            chromeVisible = false
        }
    }

    // Reset chrome visibility when leaving auto-hide range
    LaunchedEffect(scaleLevel) {
        if (scaleLevel < 3) chromeVisible = true
    }

    // ViewModels shared with GamepadNavEffect
    val faceVm: FaceViewModel = viewModel()
    val trackVm: TrackViewModel = viewModel()
    val colorVm: ColorViewModel = viewModel()
    val driveVm: DriveViewModel = viewModel()
    val pathsVm: PathsViewModel = viewModel()

    val gamepadState by gamepadManager.state.collectAsState()
    val gamepadConnected = gamepadState.connected
    val pathsList by pathsVm.paths.collectAsState()

    // Gamepad navigation effect — only fires when controller is connected
    GamepadNavEffect(
        gamepadManager = gamepadManager,
        currentTab = selectedTab,
        onTabChange = { selectedTab = it },
        faceBackend = faceBackend,
        onFaceBackendChange = { faceBackend = it },
        faceMode = faceMode,
        onFaceModeChange = { faceMode = it },
        faceVm = faceVm,
        trackBackend = trackBackend,
        onTrackBackendChange = { trackBackend = it },
        trackMode = trackMode,
        onTrackModeChange = { trackMode = it },
        trackVm = trackVm,
        driveVm = driveVm,
        pathsVm = pathsVm,
        pathsSelectedIndex = pathsSelectedIndex,
        onPathsSelectedIndexChange = { pathsSelectedIndex = it },
        pathsCount = pathsList.size,
        onScaleCycle = onScaleCycle,
        onAnyButtonPress = { lastButtonPress = System.currentTimeMillis() },
        onSensorsGridNav = if (useRail && layoutMode == "grid") { delta ->
            val cells = GridCell.entries
            val curIdx = gridExpandedCell?.let { cells.indexOf(it) } ?: -1
            gridExpandedCell = if (delta > 0) {
                cells[(curIdx + 1) % cells.size]
            } else {
                cells[if (curIdx <= 0) cells.lastIndex else curIdx - 1]
            }
        } else null,
        onSensorsPanelCycle = if (useRail) { delta ->
            if (layoutMode == "grid") {
                val count = 3 // Dashboard, Drive, Paths
                gridPanelTab = (gridPanelTab + delta + count) % count
            } else {
                val idx = railTabs.indexOf(classicActiveTab ?: railTabs[0])
                val newIdx = (idx + delta + railTabs.size) % railTabs.size
                classicActiveTab = railTabs[newIdx]
            }
        } else null,
    )

    val showChrome = chromeVisible || scaleLevel < 3

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize()) {
            AnimatedVisibility(visible = showChrome, enter = fadeIn(), exit = fadeOut()) {
                TopBar(gamepadConnected)
            }

            if (useRail) {
                if (layoutMode == "grid") {
                    GridLayout(
                        gamepadManager = gamepadManager,
                        selectedTab = selectedTab,
                        faceVm = faceVm,
                        trackVm = trackVm,
                        colorVm = colorVm,
                        driveVm = driveVm,
                        pathsVm = pathsVm,
                        faceBackend = faceBackend,
                        onFaceBackendChange = { faceBackend = it },
                        faceMode = faceMode,
                        onFaceModeChange = { faceMode = it },
                        trackBackend = trackBackend,
                        onTrackBackendChange = { trackBackend = it },
                        trackMode = trackMode,
                        onTrackModeChange = { trackMode = it },
                        colorMode = colorMode,
                        onColorModeChange = { colorMode = it },
                        pathsSelectedIndex = pathsSelectedIndex,
                        gamepadConnected = gamepadConnected,
                        showChrome = showChrome,
                        expandedCell = gridExpandedCell,
                        onExpandedCellChange = { gridExpandedCell = it },
                        panelTab = gridPanelTab,
                        onPanelTabChange = { gridPanelTab = it },
                    )
                } else {
                    TabletLandscapeLayout(
                        gamepadManager = gamepadManager,
                        selectedTab = selectedTab,
                        faceVm = faceVm,
                        trackVm = trackVm,
                        colorVm = colorVm,
                        driveVm = driveVm,
                        pathsVm = pathsVm,
                        faceBackend = faceBackend,
                        onFaceBackendChange = { faceBackend = it },
                        faceMode = faceMode,
                        onFaceModeChange = { faceMode = it },
                        trackBackend = trackBackend,
                        onTrackBackendChange = { trackBackend = it },
                        trackMode = trackMode,
                        onTrackModeChange = { trackMode = it },
                        colorMode = colorMode,
                        onColorModeChange = { colorMode = it },
                        pathsSelectedIndex = pathsSelectedIndex,
                        gamepadConnected = gamepadConnected,
                        showChrome = showChrome,
                        activeTab = classicActiveTab,
                        onActiveTabChange = { classicActiveTab = it },
                    )
                }
            } else {
                PhoneLayout(
                    gamepadManager = gamepadManager,
                    selectedTab = selectedTab,
                    onTabChange = { selectedTab = it },
                    faceVm = faceVm,
                    trackVm = trackVm,
                    colorVm = colorVm,
                    driveVm = driveVm,
                    pathsVm = pathsVm,
                    faceBackend = faceBackend,
                    onFaceBackendChange = { faceBackend = it },
                    faceMode = faceMode,
                    onFaceModeChange = { faceMode = it },
                    trackBackend = trackBackend,
                    onTrackBackendChange = { trackBackend = it },
                    trackMode = trackMode,
                    onTrackModeChange = { trackMode = it },
                    colorMode = colorMode,
                    onColorModeChange = { colorMode = it },
                    pathsSelectedIndex = pathsSelectedIndex,
                    gamepadConnected = gamepadConnected,
                    showChrome = showChrome,
                )
            }
        }

        // Gamepad controls info — bottom-left, visible when connected + chrome shown
        if (gamepadConnected) {
            AnimatedVisibility(
                visible = showChrome,
                enter = fadeIn(),
                exit = fadeOut(),
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(
                        start = if (useRail) (92 * LocalUiScale.current).dp else (12 * LocalUiScale.current).dp,
                        bottom = if (useRail) (12 * LocalUiScale.current).dp else (92 * LocalUiScale.current).dp,
                    ),
            ) {
                GamepadControlsButton(currentTab = selectedTab)
            }
        }
    }
}

@Composable
private fun TopBar(gamepadConnected: Boolean) {
    val scale = LocalUiScale.current
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height((44 * scale).dp)
            .background(
                Brush.horizontalGradient(
                    colors = listOf(GoldDark, Gold, GoldDark),
                )
            ),
        contentAlignment = Alignment.CenterStart,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = (16 * scale).dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "AUTOCAR",
                fontFamily = Rajdhani,
                fontWeight = FontWeight.Bold,
                fontSize = (22 * scale).sp,
                color = SurfaceDark,
                letterSpacing = 3.sp,
                modifier = Modifier.weight(1f),
            )
            if (gamepadConnected) {
                Icon(
                    Icons.Default.SportsEsports,
                    contentDescription = "Gamepad connected",
                    tint = SurfaceDark,
                    modifier = Modifier.size((20 * scale).dp),
                )
            }
        }
    }
}

// ── Tablet landscape: left rail (viewport) + viewport + side panel + right rail ──

@Composable
private fun ColumnScope.TabletLandscapeLayout(
    gamepadManager: GamepadManager,
    selectedTab: NavTab,
    faceVm: FaceViewModel,
    trackVm: TrackViewModel,
    colorVm: ColorViewModel,
    driveVm: DriveViewModel,
    pathsVm: PathsViewModel,
    faceBackend: String,
    onFaceBackendChange: (String) -> Unit,
    faceMode: String,
    onFaceModeChange: (String) -> Unit,
    trackBackend: String,
    onTrackBackendChange: (String) -> Unit,
    trackMode: String,
    onTrackModeChange: (String) -> Unit,
    colorMode: String,
    onColorModeChange: (String) -> Unit,
    pathsSelectedIndex: Int,
    gamepadConnected: Boolean,
    showChrome: Boolean = true,
    activeTab: NavTab?,
    onActiveTabChange: (NavTab?) -> Unit,
) {
    var viewport by remember { mutableStateOf(Viewport.Sensors) }

    // Sync gamepad tab selection to tablet viewport/panel
    LaunchedEffect(selectedTab) {
        when (selectedTab) {
            NavTab.Sensors -> { viewport = Viewport.Sensors; onActiveTabChange(null) }
            NavTab.Face -> { viewport = Viewport.Face; onActiveTabChange(null) }
            NavTab.Track -> { viewport = Viewport.Track; onActiveTabChange(null) }
            NavTab.Color -> { viewport = Viewport.Color; onActiveTabChange(null) }
            NavTab.Dashboard -> onActiveTabChange(NavTab.Dashboard)
            NavTab.Drive -> onActiveTabChange(NavTab.Drive)
            NavTab.Paths -> onActiveTabChange(NavTab.Paths)
        }
    }

    val scale = LocalUiScale.current

    Row(modifier = Modifier.weight(1f)) {
        // Left rail — viewport selector
        AnimatedVisibility(visible = showChrome, enter = fadeIn(), exit = fadeOut()) {
            NavigationRail(
                containerColor = MaterialTheme.colorScheme.surface,
            ) {
                Viewport.entries.forEach { vp ->
                    NavigationRailItem(
                        icon = { Icon(vp.icon, contentDescription = vp.label, modifier = Modifier.size((24 * scale).dp)) },
                        label = { Text(vp.label) },
                        selected = viewport == vp,
                        onClick = { viewport = vp },
                        colors = NavigationRailItemDefaults.colors(
                            selectedIconColor = Gold,
                            selectedTextColor = Gold,
                            indicatorColor = GoldDark.copy(alpha = 0.25f),
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                }
            }
        }

        // Main viewport
        Box(modifier = Modifier.weight(1f)) {
            when (viewport) {
                Viewport.Sensors -> SensorViewport()
                Viewport.Face -> FaceViewport(
                    faceVm = faceVm,
                    selectedBackend = faceBackend,
                    onBackendChange = onFaceBackendChange,
                    selectedMode = faceMode,
                    onModeChange = onFaceModeChange,
                )
                Viewport.Track -> ObjectTrackViewport(
                    trackVm = trackVm,
                    selectedBackend = trackBackend,
                    onBackendChange = onTrackBackendChange,
                    selectedMode = trackMode,
                    onModeChange = onTrackModeChange,
                )
                Viewport.Color -> ColorViewport(
                    colorVm = colorVm,
                    selectedMode = colorMode,
                    onModeChange = onColorModeChange,
                )
            }
        }

        // Side panel
        SidePanel(
            visible = activeTab != null,
            title = activeTab?.label ?: "",
            onClose = { onActiveTabChange(null) },
        ) {
            when (activeTab) {
                NavTab.Dashboard -> DashboardScreen()
                NavTab.Drive -> DriveScreen(gamepadManager = gamepadManager, driveVm = driveVm)
                NavTab.Paths -> PathsScreen(
                    pathsVm = pathsVm,
                    selectedIndex = pathsSelectedIndex,
                    gamepadConnected = gamepadConnected,
                )
                else -> {}
            }
        }

        // Right rail — side panel tabs
        AnimatedVisibility(visible = showChrome, enter = fadeIn(), exit = fadeOut()) {
            NavigationRail(
                containerColor = MaterialTheme.colorScheme.surface,
            ) {
                railTabs.forEach { tab ->
                    NavigationRailItem(
                        icon = { Icon(tab.icon, contentDescription = tab.label, modifier = Modifier.size((24 * scale).dp)) },
                        label = { Text(tab.label) },
                        selected = activeTab == tab,
                        onClick = {
                            onActiveTabChange(if (activeTab == tab) null else tab)
                        },
                        colors = NavigationRailItemDefaults.colors(
                            selectedIconColor = Gold,
                            selectedTextColor = Gold,
                            indicatorColor = GoldDark.copy(alpha = 0.25f),
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                }
            }
        }
    }
}

// ── Grid layout: left rail + 2x2 grid with hold-to-enlarge ──

/** Identifies a cell in the 2x2 grid. */
private enum class GridCell { Camera, Depth, Lidar, Panel }

@Composable
private fun ColumnScope.GridLayout(
    gamepadManager: GamepadManager,
    selectedTab: NavTab,
    faceVm: FaceViewModel,
    trackVm: TrackViewModel,
    colorVm: ColorViewModel,
    driveVm: DriveViewModel,
    pathsVm: PathsViewModel,
    faceBackend: String,
    onFaceBackendChange: (String) -> Unit,
    faceMode: String,
    onFaceModeChange: (String) -> Unit,
    trackBackend: String,
    onTrackBackendChange: (String) -> Unit,
    trackMode: String,
    onTrackModeChange: (String) -> Unit,
    colorMode: String,
    onColorModeChange: (String) -> Unit,
    pathsSelectedIndex: Int,
    gamepadConnected: Boolean,
    showChrome: Boolean = true,
    expandedCell: GridCell?,
    onExpandedCellChange: (GridCell?) -> Unit,
    panelTab: Int,
    onPanelTabChange: (Int) -> Unit,
) {
    val sensorsVm: SensorsViewModel = viewModel()
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    val cameraUrl by sensorsVm.cameraUrl.collectAsState()
    val depthUrl by sensorsVm.depthUrl.collectAsState()

    LaunchedEffect(Unit) { sensorsVm.connect() }

    var viewport by remember { mutableStateOf(Viewport.Sensors) }
    val panelTabs = listOf("Dashboard", "Drive", "Paths")

    // Sync gamepad tab selection to grid viewport/panel
    LaunchedEffect(selectedTab) {
        when (selectedTab) {
            NavTab.Sensors -> { viewport = Viewport.Sensors; onExpandedCellChange(null) }
            NavTab.Face -> { viewport = Viewport.Face; onExpandedCellChange(null) }
            NavTab.Track -> { viewport = Viewport.Track; onExpandedCellChange(null) }
            NavTab.Color -> { viewport = Viewport.Color; onExpandedCellChange(null) }
            NavTab.Dashboard -> { onExpandedCellChange(GridCell.Panel); onPanelTabChange(0) }
            NavTab.Drive -> { onExpandedCellChange(GridCell.Panel); onPanelTabChange(1) }
            NavTab.Paths -> { onExpandedCellChange(GridCell.Panel); onPanelTabChange(2) }
        }
    }

    val scale = LocalUiScale.current

    Row(modifier = Modifier.weight(1f)) {
        // Left rail — viewport selector (same as classic)
        AnimatedVisibility(visible = showChrome, enter = fadeIn(), exit = fadeOut()) {
            NavigationRail(
                containerColor = MaterialTheme.colorScheme.surface,
            ) {
                Viewport.entries.forEach { vp ->
                    NavigationRailItem(
                        icon = { Icon(vp.icon, contentDescription = vp.label, modifier = Modifier.size((24 * scale).dp)) },
                        label = { Text(vp.label) },
                        selected = viewport == vp && expandedCell == null,
                        onClick = {
                            viewport = vp
                            onExpandedCellChange(null)
                        },
                        colors = NavigationRailItemDefaults.colors(
                            selectedIconColor = Gold,
                            selectedTextColor = Gold,
                            indicatorColor = GoldDark.copy(alpha = 0.25f),
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                }
            }
        }

        // Main area — either expanded single cell or 2x2 grid
        if (expandedCell != null) {
            Box(modifier = Modifier.weight(1f).fillMaxSize()) {
                when (expandedCell) {
                    GridCell.Camera -> MjpegView(url = cameraUrl, modifier = Modifier.fillMaxSize())
                    GridCell.Depth -> MjpegView(url = depthUrl, modifier = Modifier.fillMaxSize())
                    GridCell.Lidar -> LidarPolarPlot(
                        points = sensorUpdate.lidar?.points ?: emptyList(),
                        modifier = Modifier.fillMaxSize(),
                    )
                    GridCell.Panel -> Column(Modifier.fillMaxSize()) {
                        TabRow(
                            selectedTabIndex = panelTab,
                            containerColor = MaterialTheme.colorScheme.surface,
                            contentColor = Gold,
                            indicator = { tabPositions ->
                                TabRowDefaults.SecondaryIndicator(
                                    modifier = Modifier.tabIndicatorOffset(tabPositions[panelTab]),
                                    color = Gold,
                                )
                            },
                        ) {
                            panelTabs.forEachIndexed { i, title ->
                                Tab(
                                    selected = panelTab == i,
                                    onClick = { onPanelTabChange(i) },
                                    text = { Text(title) },
                                    selectedContentColor = Gold,
                                    unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        Box(Modifier.weight(1f)) {
                            when (panelTab) {
                                0 -> DashboardScreen()
                                1 -> DriveScreen(gamepadManager = gamepadManager, driveVm = driveVm)
                                2 -> PathsScreen(
                                    pathsVm = pathsVm,
                                    selectedIndex = pathsSelectedIndex,
                                    gamepadConnected = gamepadConnected,
                                )
                            }
                        }
                    }
                    null -> {}
                }
                // Close button overlay
                IconButton(
                    onClick = { onExpandedCellChange(null) },
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(8.dp)
                        .size(36.dp)
                        .background(
                            MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                            CircleShape,
                        ),
                ) {
                    Icon(Icons.Default.Close, contentDescription = "Close", tint = Gold)
                }
            }
        } else when (viewport) {
            // Face / Track — full viewport like classic layout
            Viewport.Face -> Box(modifier = Modifier.weight(1f)) {
                FaceViewport(
                    faceVm = faceVm,
                    selectedBackend = faceBackend,
                    onBackendChange = onFaceBackendChange,
                    selectedMode = faceMode,
                    onModeChange = onFaceModeChange,
                )
            }
            Viewport.Track -> Box(modifier = Modifier.weight(1f)) {
                ObjectTrackViewport(
                    trackVm = trackVm,
                    selectedBackend = trackBackend,
                    onBackendChange = onTrackBackendChange,
                    selectedMode = trackMode,
                    onModeChange = onTrackModeChange,
                )
            }
            Viewport.Color -> Box(modifier = Modifier.weight(1f)) {
                ColorViewport(
                    colorVm = colorVm,
                    selectedMode = colorMode,
                    onModeChange = onColorModeChange,
                )
            }

            // Sensors — 2x2 grid with hold-to-enlarge
            Viewport.Sensors -> {
            // Two-column layout: cameras left, lidar + panel right
            Row(modifier = Modifier.weight(1f)) {
                // Left column — RGB + Depth stacked (wider to show feeds)
                Column(
                    modifier = Modifier
                        .weight(1.4f)
                        .fillMaxSize(),
                ) {
                    // Camera RGB
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { onExpandedCellChange(GridCell.Camera) })
                            },
                    ) {
                        MjpegView(url = cameraUrl, modifier = Modifier.fillMaxSize())
                    }
                    // Depth
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { onExpandedCellChange(GridCell.Depth) })
                            },
                    ) {
                        MjpegView(url = depthUrl, modifier = Modifier.fillMaxSize())
                    }
                }
                // Right column — Lidar + Panel
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxSize(),
                ) {
                    // Lidar (constrained to not overflow)
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .clipToBounds()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { onExpandedCellChange(GridCell.Lidar) })
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        LidarPolarPlot(
                            points = sensorUpdate.lidar?.points ?: emptyList(),
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                    // Panel content (Dashboard/Drive/Paths)
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { onExpandedCellChange(GridCell.Panel) })
                            },
                    ) {
                        Column(Modifier.fillMaxSize()) {
                            TabRow(
                                selectedTabIndex = panelTab,
                                containerColor = MaterialTheme.colorScheme.surface,
                                contentColor = Gold,
                                indicator = { tabPositions ->
                                    TabRowDefaults.SecondaryIndicator(
                                        modifier = Modifier.tabIndicatorOffset(tabPositions[panelTab]),
                                        color = Gold,
                                    )
                                },
                            ) {
                                panelTabs.forEachIndexed { i, title ->
                                    Tab(
                                        selected = panelTab == i,
                                        onClick = { onPanelTabChange(i) },
                                        text = { Text(title) },
                                        selectedContentColor = Gold,
                                        unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            Box(Modifier.weight(1f)) {
                                when (panelTab) {
                                    0 -> DashboardScreen()
                                    1 -> DriveScreen(gamepadManager = gamepadManager, driveVm = driveVm)
                                    2 -> PathsScreen(
                                        pathsVm = pathsVm,
                                        selectedIndex = pathsSelectedIndex,
                                        gamepadConnected = gamepadConnected,
                                    )
                                }
                            }
                        }
                    }
                }
            }
            }
        }
    }
}

// ── Phone / tablet portrait: bottom nav with full-screen tabs ──

@Composable
private fun ColumnScope.PhoneLayout(
    gamepadManager: GamepadManager,
    selectedTab: NavTab,
    onTabChange: (NavTab) -> Unit,
    faceVm: FaceViewModel,
    trackVm: TrackViewModel,
    colorVm: ColorViewModel,
    driveVm: DriveViewModel,
    pathsVm: PathsViewModel,
    faceBackend: String,
    onFaceBackendChange: (String) -> Unit,
    faceMode: String,
    onFaceModeChange: (String) -> Unit,
    trackBackend: String,
    onTrackBackendChange: (String) -> Unit,
    trackMode: String,
    onTrackModeChange: (String) -> Unit,
    colorMode: String,
    onColorModeChange: (String) -> Unit,
    pathsSelectedIndex: Int,
    gamepadConnected: Boolean,
    showChrome: Boolean = true,
) {
    Box(modifier = Modifier.weight(1f)) {
        when (selectedTab) {
            NavTab.Sensors -> SensorViewport()
            NavTab.Face -> FaceViewport(
                faceVm = faceVm,
                selectedBackend = faceBackend,
                onBackendChange = onFaceBackendChange,
                selectedMode = faceMode,
                onModeChange = onFaceModeChange,
            )
            NavTab.Track -> ObjectTrackViewport(
                trackVm = trackVm,
                selectedBackend = trackBackend,
                onBackendChange = onTrackBackendChange,
                selectedMode = trackMode,
                onModeChange = onTrackModeChange,
            )
            NavTab.Color -> ColorViewport(
                colorVm = colorVm,
                selectedMode = colorMode,
                onModeChange = onColorModeChange,
            )
            NavTab.Dashboard -> DashboardScreen()
            NavTab.Drive -> DriveScreen(gamepadManager = gamepadManager, driveVm = driveVm)
            NavTab.Paths -> PathsScreen(
                pathsVm = pathsVm,
                selectedIndex = pathsSelectedIndex,
                gamepadConnected = gamepadConnected,
            )
        }

    }

    AnimatedVisibility(visible = showChrome, enter = fadeIn(), exit = fadeOut()) {
        val scale = LocalUiScale.current
        NavigationBar(
            containerColor = MaterialTheme.colorScheme.surface,
        ) {
            NavTab.entries.forEach { tab ->
                NavigationBarItem(
                    icon = { Icon(tab.icon, contentDescription = tab.label, modifier = Modifier.size((24 * scale).dp)) },
                    label = { Text(tab.label) },
                    selected = selectedTab == tab,
                    onClick = { onTabChange(tab) },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Gold,
                        selectedTextColor = Gold,
                        indicatorColor = GoldDark.copy(alpha = 0.25f),
                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
            }
        }
    }
}
