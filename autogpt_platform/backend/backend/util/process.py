"""
process.py - Utilidades para la gestión de procesos en segundo plano en AutoGPT Platform.

Este módulo define la clase base AppProcess, que permite ejecutar componentes de la aplicación como procesos independientes,
proporcionando mecanismos para iniciar, detener, limpiar y monitorear procesos de manera segura y extensible.
Incluye utilidades para el manejo de nombres de servicio, configuración de logging y métricas, y control de señales.
"""

import logging
import os
import signal
import sys
from abc import ABC, abstractmethod
from multiprocessing import Process, set_start_method
from typing import Optional

from backend.util.logging import configure_logging
from backend.util.metrics import sentry_init

logger = logging.getLogger(__name__)
_SERVICE_NAME = "MainProcess"


def get_service_name():
    """
    Retorna el nombre del servicio actual para propósitos de logging y monitoreo.
    """
    return _SERVICE_NAME


def set_service_name(name: str):
    """
    Establece el nombre global del servicio para el proceso actual.
    """
    global _SERVICE_NAME
    _SERVICE_NAME = name


class AppProcess(ABC):
    """
    Clase base abstracta para representar un componente ejecutable en un proceso independiente.

    Heredar de AppProcess permite definir servicios o tareas que pueden ejecutarse en segundo plano o en primer plano,
    con soporte para manejo de señales, logging, métricas y limpieza de recursos.

    Métodos a sobrescribir:
        - run(): Lógica principal del proceso.
        - cleanup(): (opcional) Limpieza de recursos al finalizar el proceso.
        - health_check(): (opcional) Verificación de salud personalizada.
    """

    process: Optional[Process] = None

    set_start_method("spawn", force=True)
    configure_logging()
    sentry_init()

    @abstractmethod
    def run(self):
        """
        Método principal que se ejecuta dentro del proceso hijo.
        Debe ser implementado por las subclases para definir la lógica del proceso.
        """
        pass

    @classmethod
    @property
    def service_name(cls) -> str:
        """
        Retorna el nombre del servicio, por defecto el nombre de la clase.
        """
        return cls.__name__

    def cleanup(self):
        """
        Método opcional para limpiar recursos después de la ejecución del proceso.
        Sobrescribir en subclases si se requiere cerrar conexiones, archivos, etc.
        """
        pass

    def health_check(self):
        """
        Método opcional para verificar la salud del proceso.
        Puede ser sobrescrito para implementar chequeos personalizados.
        """
        pass

    def execute_run_command(self, silent):
        """
        Ejecuta el método run() dentro del proceso hijo, configurando el entorno y manejando señales.
        Si 'silent' es True, redirige stdout y stderr a /dev/null.
        """
        signal.signal(signal.SIGTERM, self._self_terminate)

        try:
            if silent:
                sys.stdout = open(os.devnull, "w")
                sys.stderr = open(os.devnull, "w")

            set_service_name(self.service_name)
            logger.info(f"[{self.service_name}] Starting...")
            self.run()
        except (KeyboardInterrupt, SystemExit) as e:
            logger.warning(f"[{self.service_name}] Terminated: {e}; quitting...")

    def _self_terminate(self, signum: int, frame):
        """
        Manejador de señal SIGTERM para realizar limpieza antes de salir.
        """
        self.cleanup()
        sys.exit(0)

    def __enter__(self):
        """
        Permite usar AppProcess como contexto, iniciando el proceso en segundo plano.
        """
        self.start(background=True)
        return self

    def __exit__(self, *args, **kwargs):
        """
        Detiene el proceso al salir del contexto.
        """
        self.stop()

    def start(self, background: bool = False, silent: bool = False, **proc_args) -> int:
        """
        Inicia el proceso.

        Args:
            background (bool): Si es True, ejecuta en segundo plano.
            silent (bool): Si es True, suprime stdout y stderr.
            proc_args: Argumentos adicionales para multiprocessing.Process.
        Returns:
            int: PID del proceso si es en segundo plano, 0 si es en primer plano.
        """
        if not background:
            self.execute_run_command(silent)
            return 0

        self.process = Process(
            name=self.__class__.__name__,
            target=self.execute_run_command,
            args=(silent,),
            **proc_args,
        )
        self.process.start()
        self.health_check()
        return self.process.pid or 0

    def stop(self):
        """
        Detiene el proceso en segundo plano y ejecuta la limpieza de recursos.
        """
        if not self.process:
            return

        self.process.terminate()
        self.process.join()
        self.cleanup()
        self.process = None
