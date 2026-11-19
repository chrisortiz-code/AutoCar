package com.autocar.app.data.ssh

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.connection.channel.direct.Session
import net.schmizz.sshj.connection.channel.direct.Signal
import net.schmizz.sshj.transport.verification.PromiscuousVerifier
import org.bouncycastle.jce.provider.BouncyCastleProvider
import java.io.BufferedReader
import java.io.InputStreamReader
import java.security.Security

class SshManager {

    companion object {
        init {
            // Replace Android's built-in BC provider with the full one that supports x25519
            Security.removeProvider("BC")
            Security.insertProviderAt(BouncyCastleProvider(), 1)
        }
    }

    private var client: SSHClient? = null

    suspend fun connect(host: String, username: String, password: String) = withContext(Dispatchers.IO) {
        disconnect()
        val ssh = SSHClient()
        ssh.addHostKeyVerifier(PromiscuousVerifier())
        ssh.connect(host, 22)
        ssh.authPassword(username, password)
        client = ssh
    }

    fun isConnected(): Boolean = client?.isConnected == true

    /**
     * Execute a command and stream its stdout+stderr line-by-line as a Flow.
     * Returns an [SshProcess] that allows stopping the remote process.
     */
    fun execStreaming(command: String): SshProcess {
        val ssh = client ?: throw IllegalStateException("Not connected")
        val session = ssh.startSession()
        session.allocateDefaultPTY()
        val cmd = session.exec(command)

        val outputFlow: Flow<String> = callbackFlow {
            val stdoutReader = BufferedReader(InputStreamReader(cmd.inputStream))
            val stderrReader = BufferedReader(InputStreamReader(cmd.errorStream))

            val stderrThread = Thread {
                try {
                    stderrReader.lineSequence().forEach { line ->
                        trySend("[stderr] $line")
                    }
                } catch (_: Exception) {}
            }
            stderrThread.isDaemon = true
            stderrThread.start()

            try {
                stdoutReader.lineSequence().forEach { line ->
                    if (!isActive) return@forEach
                    trySend(line)
                }
            } catch (_: Exception) {}

            stderrThread.join(1000)
            awaitClose()
        }

        return SshProcess(session, cmd, outputFlow)
    }

    fun disconnect() {
        try { client?.disconnect() } catch (_: Exception) {}
        client = null
    }
}

class SshProcess(
    private val session: Session,
    private val command: Session.Command,
    val output: Flow<String>,
) {
    fun stop() {
        try { command.signal(Signal.TERM) } catch (_: Exception) {}
        try {
            command.close()
            session.close()
        } catch (_: Exception) {}
    }

    val isOpen: Boolean get() = command.isOpen
}
