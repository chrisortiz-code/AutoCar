package com.autocar.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed

@Composable
fun ConnectionBar(connected: Boolean, modifier: Modifier = Modifier) {
    val bgColor by animateColorAsState(
        targetValue = if (connected) StatusGreen else StatusRed,
        label = "connection_bar_color",
    )

    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(bgColor)
            .padding(vertical = 4.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = if (connected) "Connected" else "Disconnected",
            color = Color.White,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}
