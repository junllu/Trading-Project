#!/bin/bash
# Puts a "Trading Portal" launcher on your macOS Desktop.
# Double-click this once; afterwards launch the portal from the Desktop icon.

cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
DESKTOP="$HOME/Desktop"
LINK="$DESKTOP/Trading Portal.command"

chmod +x "$PROJECT_DIR/launch.command"

cat > "$LINK" <<EOF
#!/bin/bash
# Trading Portal desktop launcher (generated).
"$PROJECT_DIR/launch.command"
EOF
chmod +x "$LINK"

echo "✅ Installed: $LINK"
echo "You can now start the portal by double-clicking 'Trading Portal' on your Desktop."
echo ""
echo "Tip: to give it an icon, right-click the Desktop file → Get Info → drag an image onto the icon."
read -r -p "Press Return to close."
