import asyncio
import ipaddress
import socket
import xmlrpc.client
from typing import Dict, List, Optional


def get_local_subnet() -> str:
    """Detecta a sub-rede local do PC (ex: 192.168.1.0/24)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    except Exception:
        return "192.168.1.0/24"


async def check_port(ip: str, port: int, timeout: float = 0.6) -> bool:
    """Verifica se uma porta TCP esta aberta num determinado IP."""
    try:
        conn = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def query_ros_master_topics(ip: str, port: int = 11311) -> List[str]:
    """Consulta o XML-RPC do ROS Master do Scout para listar os topicos ativos."""
    uri = f"http://{ip}:{port}"
    try:
        proxy = xmlrpc.client.ServerProxy(uri)
        code, msg, val = proxy.getPublishedTopics("/scout_scanner", "")
        if code == 1 and isinstance(val, list):
            return [t[0] for t in val]
    except Exception:
        pass
    return []


async def inspect_candidate(ip: str) -> Optional[Dict]:
    """Testa se o IP pertence a um Moorebot Scout verificando as portas 11311 (ROS) e 22 (SSH)."""
    ros_open = await check_port(ip, 11311, timeout=0.7)
    if not ros_open:
        return None

    ssh_open = await check_port(ip, 22, timeout=0.7)
    topics = await asyncio.to_thread(query_ros_master_topics, ip, 11311)
    is_scout = any(
        "CoreNode" in t or "SensorNode" in t or "MotorNode" in t or "cmd_vel" in t
        for t in topics
    )
    return {
        "ip": ip,
        "ros_master_port": 11311,
        "ssh_port_open": ssh_open,
        "is_confirmed_scout": is_scout or len(topics) > 0,
        "topics": topics,
    }


async def scan_network_for_scout(subnet: Optional[str] = None) -> List[Dict]:
    """Varre toda a sub-rede local (e tambem o IP 10.42.0.1 do modo AP Direto) em busca do Scout."""
    target_subnet = subnet or get_local_subnet()
    net = ipaddress.ip_network(target_subnet, strict=False)
    ips = [str(host) for host in net.hosts()]
    if "10.42.0.1" not in ips:
        ips.insert(0, "10.42.0.1")  # IP padrao do Scout em modo Wi-Fi Direct

    tasks = [inspect_candidate(ip) for ip in ips]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


if __name__ == "__main__":
    print("A procurar o Moorebot Scout na rede local (porta ROS 11311)...")
    found = asyncio.run(scan_network_for_scout())
    if not found:
        print("Nenhum Moorebot Scout encontrado. Certifique-se de que esta ligado e na mesma rede Wi-Fi.")
    else:
        for robot in found:
            print(f"\n[ENCONTRADO] IP: {robot['ip']}:11311 (SSH aberto: {robot['ssh_port_open']})")
            print(f"Topicos ROS detetados ({len(robot['topics'])}): {', '.join(robot['topics'][:10])}")
