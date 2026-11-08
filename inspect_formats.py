"""Bounded local binary inspection for the owned game and ForzaTechStudio."""
import argparse
import bisect
import struct


def native(args):
    import capstone
    import pefile
    pe = pefile.PE(str(args.file), fast_load=True)
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    start = int(args.method, 0)
    for instruction in decoder.disasm(pe.get_data(start, args.size), start):
        print("%08X %-9s %s" % (instruction.address, instruction.mnemonic, instruction.op_str))


def cil(args):
    import dnfile
    from dncil.cil.body import CilMethodBody
    from dncil.cil.body.reader import CilMethodBodyReaderBytes
    pe = dnfile.dnPE(str(args.file))
    tables = {0x02: "TypeDef", 0x04: "Field", 0x06: "MethodDef", 0x0A: "MemberRef",
              0x01: "TypeRef", 0x2B: "MethodSpec"}
    owners = {}
    for definition in pe.net.mdtables.TypeDef:
        owner = str(definition.TypeNamespace) + "." + str(definition.TypeName)
        for method in definition.MethodList:
            owners[id(method.row)] = owner
        for field in definition.FieldList:
            owners[id(field.row)] = owner

    def token_name(token):
        value = token.value
        kind, index = value >> 24, (value & 0xFFFFFF) - 1
        if kind == 0x70:
            item = pe.net.user_strings.get(value & 0xFFFFFF)
            return repr(item.value) if item else str(token)
        table = getattr(pe.net.mdtables, tables.get(kind, ""), None)
        if table is None or index < 0 or index >= len(table.rows):
            return str(token)
        row = table.rows[index]
        name = getattr(row, "Name", getattr(row, "TypeName", token))
        owner = owners.get(id(row), "")
        if kind == 0x0A and row.Class.row is not None:
            owner = str(getattr(row.Class.row, "TypeName", ""))
        return owner + "." + str(name)

    for definition in pe.net.mdtables.TypeDef:
        if str(definition.TypeName) != args.type:
            continue
        for method in definition.MethodList:
            row = method.row
            if str(row.Name) != args.method or not row.Rva:
                continue
            print("%s.%s RVA=%X" % (args.type, row.Name, row.Rva))
            body = CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(row.Rva, args.size)))
            for instruction in body.instructions:
                operand = instruction.operand
                if hasattr(operand, "value"):
                    operand = token_name(operand)
                print("%04X %-12s %s" % (instruction.offset, instruction.opcode.name, operand or ""))


def xrefs(args):
    import pefile
    pe = pefile.PE(str(args.file), fast_load=True)
    targets = []
    if args.method.startswith("0x"):
        targets.append(int(args.method, 16))
    else:
        needle = args.method.encode("ascii")
        start = 0
        while True:
            offset = pe.__data__.find(needle, start)
            if offset < 0:
                break
            beginning = pe.__data__.rfind(b"\0", max(0, offset - 1024), offset) + 1
            targets.append(pe.get_rva_from_offset(beginning))
            start = offset + len(needle)
    pdata = next(section for section in pe.sections if section.Name.startswith(b".pdata"))
    functions = [record for record in struct.iter_unpack("<III", pdata.get_data()[:pdata.Misc_VirtualSize])
                 if record[0] and record[1]]
    starts = [record[0] for record in functions]
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        data, base = section.get_data(), section.VirtualAddress
        for offset in range(len(data) - 7):
            if data[offset] not in (0x48, 0x4C) or data[offset + 1] not in (0x8D, 0x8B, 0x89):
                continue
            if data[offset + 2] & 0xC7 != 5:
                continue
            target = base + offset + 7 + struct.unpack_from("<i", data, offset + 3)[0]
            if target not in targets:
                continue
            rva = base + offset
            index = bisect.bisect_right(starts, rva) - 1
            record = functions[index] if index >= 0 else (0, 0, 0)
            print("xref=%08X function=%08X end=%08X string=%08X" % (rva, record[0], record[1], target))


def calls(args):
    import pefile
    pe = pefile.PE(str(args.file), fast_load=True)
    target = int(args.method, 0)
    pdata = next(section for section in pe.sections if section.Name.startswith(b".pdata"))
    functions = [record for record in struct.iter_unpack("<III", pdata.get_data()[:pdata.Misc_VirtualSize])
                 if record[0] and record[1]]
    starts = [record[0] for record in functions]
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        data, base = section.get_data(), section.VirtualAddress
        for offset in range(len(data) - 5):
            if data[offset] != 0xE8:
                continue
            if base + offset + 5 + struct.unpack_from("<i", data, offset + 1)[0] != target:
                continue
            rva = base + offset
            index = bisect.bisect_right(starts, rva) - 1
            record = functions[index] if index >= 0 else (0, 0, 0)
            print("call=%08X function=%08X end=%08X" % (rva, record[0], record[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("native", "cil", "xrefs", "calls"))
    parser.add_argument("file")
    parser.add_argument("method", help="Native RVA (also for xrefs), ASCII string, or managed method name")
    parser.add_argument("--type", default="MiniZipService")
    parser.add_argument("--size", type=lambda value: int(value, 0), default=0x10000)
    args = parser.parse_args()
    {"native": native, "cil": cil, "xrefs": xrefs, "calls": calls}[args.mode](args)


if __name__ == "__main__":
    main()
