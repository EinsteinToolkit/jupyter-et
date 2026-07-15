set -ex

function cleanup() {
    telegram-send "Exited Tutorial Server Push"
}

trap cleanup EXIT
for img in base notebook deploy-hook cilogon cyol
do
    echo "PUSHING IMAGE: $img"
    docker push einsteintoolkit/et-$img
    telegram-send "PUSHED IMAGE: $img"
done
