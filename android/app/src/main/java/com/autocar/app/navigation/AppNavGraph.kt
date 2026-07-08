package com.autocar.app.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Gamepad
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.screens.DriveScreen
import com.autocar.app.ui.screens.HomeScreen
import com.autocar.app.ui.screens.PathsScreen
import com.autocar.app.ui.screens.SensorsScreen
import com.autocar.app.ui.screens.SettingsScreen

enum class Screen(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "Home", Icons.Default.Home),
    Drive("drive", "Drive", Icons.Default.Gamepad),
    Sensors("sensors", "Sensors", Icons.Default.Sensors),
    Paths("paths", "Paths", Icons.Default.Route),
    Settings("settings", "Settings", Icons.Default.Settings),
}

@Composable
fun AppNavGraph(gamepadManager: GamepadManager) {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = navBackStackEntry?.destination

    Scaffold(
        bottomBar = {
            NavigationBar {
                Screen.entries.forEach { screen ->
                    NavigationBarItem(
                        icon = { Icon(screen.icon, contentDescription = screen.label) },
                        label = { Text(screen.label) },
                        selected = currentDestination?.hierarchy?.any { it.route == screen.route } == true,
                        onClick = {
                            navController.navigate(screen.route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                    )
                }
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = Screen.Home.route,
            modifier = Modifier.padding(innerPadding),
        ) {
            composable(Screen.Home.route) { HomeScreen() }
            composable(Screen.Drive.route) { DriveScreen(gamepadManager = gamepadManager) }
            composable(Screen.Sensors.route) { SensorsScreen() }
            composable(Screen.Paths.route) { PathsScreen() }
            composable(Screen.Settings.route) { SettingsScreen() }
        }
    }
}
